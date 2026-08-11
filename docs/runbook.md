# Running and troubleshooting the lab

## Start here

No containers needed for the main arc:

```bash
cp .env.example .env
.venv/bin/python -m pytest -q
./scripts/demo.sh
```

With containers and Hermes:

```bash
podman machine start                 # if the default machine is stopped
podman compose up -d --build
./scripts/smoke.sh
ENTERPRISE_API_URL=http://127.0.0.1:8080 ./scripts/mcp-smoke.sh
ENTERPRISE_API_URL=http://127.0.0.1:8080 ./scripts/hermes-tool-filter-proof.sh
podman compose down -v
```

`workflow-runner` runs to completion and exits 0 by design. Some compose
providers treat that as a failure under `--wait`; the API service is the one that
must be healthy.

## Health checks

| Endpoint | Meaning |
|---|---|
| `GET /healthz` | Process is alive |
| `GET /readyz` | Service finished startup and can serve traffic |

## Scripts

| Command | Purpose |
|---|---|
| `./scripts/demo.sh` | Full flow; starts its own API unless `ENTERPRISE_API_URL` is already healthy |
| `./scripts/smoke.sh` | Containerized workflow-runner receipt |
| `./scripts/mcp-smoke.sh` | FastMCP protocol check (explicit credentials plus a wrong-token check) and Hermes discovery |
| `MCP_SMOKE_PROTOCOL_ONLY=1 ./scripts/mcp-smoke.sh` | Protocol check only (CI, no Hermes CLI) |
| `./scripts/hermes-mcp-proof.sh` | Check Hermes discovery in an isolated home directory |
| `./scripts/hermes-tool-filter-proof.sh` | Change the server allowlist and show Hermes seeing 4 tools, then 1 |
| `./scripts/fresh-clone-check.sh` | Clone HEAD to a temp dir, fresh venv, full suite + demo |
| `./scripts/record-demo.sh` | asciinema cast if available, otherwise a text transcript + a recording plan |
| `./scripts/emit-hermes-mcp-config.sh <root> [tools]` | Emit Hermes config with absolute paths and a server-side allowlist |

`fastmcp inspect` is fine for a capability summary. Do **not** use the fastmcp
CLI's command-spawning subcommands to test credential handling: they cannot pass
`env` to the server, and MCP stdio forwards only
`HOME`, `LOGNAME`, `PATH`, `SHELL`, `USER`. That is exactly how this repo once
"proved" credentials that were never delivered — see ADR 004.

## Hermes MCP test (isolated)

```bash
export ENTERPRISE_API_TOKEN=lab-read-token
export HERMES_HOME=/tmp/hermes-mcp-lab
mkdir -p "$HERMES_HOME"
{
  echo "_config_version: 9"
  ./scripts/emit-hermes-mcp-config.sh "$PWD" all
} > "$HERMES_HOME/config.yaml"
hermes mcp test enterprise_ops
```

Hermes prints **bare** tool names (`check_enterprise_api`, …). It does not print
the `mcp__enterprise_ops__*` prefixed form, so do not expect it in the output.

`hermes mcp test` proves discovery only: no LLM, no provider call, no invocation.

## Troubleshooting

### `enterprise-mcp configuration error: ENTERPRISE_API_TOKEN is not set`

Working as designed. The server has no default token. Pass it explicitly in the
client's stdio `env=`, or in the Hermes `env:` block. Exporting it in your shell
is **not** enough for an MCP subprocess.

### Smoke fails with connection refused on port 8080

1. `podman compose ps`
2. `podman compose logs enterprise-api`
3. `podman machine start`

### MCP smoke fails before the protocol calls

`ENTERPRISE_API_URL` must point at the published Compose port
(`http://127.0.0.1:8080` from the host). Inside containers use
`http://enterprise-api:8080`.

### MCP smoke fails: hermes CLI not found

Full smoke requires Hermes on PATH. For protocol-only proof:
`MCP_SMOKE_PROTOCOL_ONLY=1 ./scripts/mcp-smoke.sh`.

### `ModuleNotFoundError: workflow_runner`

Set `PYTHONPATH` to include `<repo>/enterprise-mcp` and `<repo>/workflow-runner`
in the MCP server env, or install the requirements files.

### `401 Missing bearer token`

No `Authorization: Bearer …` header was sent.

### `403` on a mutation / `auth_failure` with `credential_scope: read_token_only`

The write endpoint requires `ENTERPRISE_API_WRITE_TOKEN`, which is a different
value from the read token. Set it in `.env` and in the MCP server env.

### `apply_incident_plan` keeps returning `pending_approval`

That is the approval gate working as designed: the caller only receives an
opaque `approval_id`. A separate operator must grant it:

```bash
export PYTHONPATH="$PWD/workflow-runner"
export APPROVAL_STORE_PATH="$PWD/.audit/demo-approvals.json"
export AUDIT_LOG_PATH="$PWD/.audit/demo-audit.jsonl"
python -m workflow_runner.approval_operator approve <approval_id> \
  --approver operator@example.com
```

Deliver the returned `approval_capability` to the MCP caller without logging it.
The capability is bound to one `incident_id` + `action_id`, expires after
`APPROVAL_TTL_SECONDS` (900 by default), and is terminal after a confirmed apply.
The identity is recorded but not authenticated in this lab.

### `approval_rejected: unknown_action_id`

`action_id` must be a `step_id` from the runbook, e.g. `RB-PAY-GATEWAY-01-S2`.
`propose_incident_plan` returns them.

### `approval_rejected: approval_capability_bound_to_different_action`

The capability was granted for another step. Request a fresh approval ID by
calling without a capability, then have an operator grant it.

### `approval_rejected: approval_expired` or `approval_already_applied`

Expired and applied approvals are terminal. Request and grant a new approval.
Do not retry a capability after a confirmed `applied` or `replayed` response.

### A mutation returned 500 — did it apply?

Check `resume.resumable` in the response, then re-invoke with the same
`approval_capability`. If the write had already committed, you get
`status: replayed` and no second record. Confirm with:

```bash
AUTHORIZATION_HEADER="Authorization: Bearer <fixture-token>" # gitleaks:allow -- documented placeholder
curl -s -H "$AUTHORIZATION_HEADER" \
  http://127.0.0.1:8080/v1/incidents/INC-2026-0042/actions | python3 -m json.tool
```

`count` must be 1.

### Resetting the fixture store

```bash
AUTHORIZATION_HEADER="Authorization: Bearer <fixture-token>" # gitleaks:allow -- documented placeholder
curl -X POST -H "$AUTHORIZATION_HEADER" \
  http://127.0.0.1:8080/v1/admin/reset-actions
```

### Injected failures during drills

| Trigger | Expected |
|---|---|
| `?inject=error` | 500, nothing persisted |
| `?inject=error_after_commit` | 500, record **is** persisted — resume must replay |
| `?inject=timeout` | Sleeps `INJECT_TIMEOUT_SECONDS` |

Clear `ENTERPRISE_INJECT_FAILURE` to recover.

### Receipt shows `outcome: error`

| code | Meaning | Recovery |
|---|---|---|
| `auth_failure` | Token rejected or wrong scope | Check read vs write token |
| `not_found` | Incident/runbook missing | Use `INC-2026-0042` |
| `bad_request` | Missing idempotency key | The workflow layer supplies it; a raw curl must set `Idempotency-Key` |
| `timeout` | Upstream too slow | Check API health, clear injection |
| `malformed_response` | Non-JSON or wrong shape | `podman compose restart enterprise-api` |
| `upstream_5xx` | API error, possibly injected | Clear injection, then resume with the same approval capability before expiry |

### Reading the audit trail

```bash
python3 -m json.tool < /dev/null  # (jq is not required)
cat .audit/demo-audit.jsonl | while read -r line; do
  python3 -c "import json,sys; e=json.loads(sys.argv[1]); print(e['seq'], e['event'], e.get('outcome'), e.get('correlation_id'))" "$line"
done
```

## Receipt locations

| Path | Contents |
|---|---|
| `.smoke-receipts/workflow-receipt.json` | Workflow runner smoke receipt |
| `.smoke-receipts/demo-receipt.json` | Demo arc result and step summary |
| `.mcp-receipts/mcp-smoke-receipt.json` | Protocol + Hermes discovery summary |
| `.mcp-receipts/fastmcp-protocol.json` | Protocol checks including the negative control |
| `.mcp-receipts/hermes-mcp-proof.json` | Hermes discovery receipt |
| `.mcp-receipts/hermes-tool-filter-proof.json` | Differential scope proof |
| `.audit/*.jsonl` | Append-only audit trail |

All are gitignored.

## Security notes

- Do not commit `.env`.
- Do not embed tokens in committed YAML; use `${ENTERPRISE_API_TOKEN}`.
- Do not merge lab MCP config into live `~/.hermes/config.yaml`.
- Receipts and audit logs are local proof only; scrub before sharing.
