# Running and troubleshooting the lab

## Start here

No containers needed for the main arc:

```bash
cp .env.example .env
.venv/bin/python -m pytest -q
./scripts/demo.sh
```

Self-contained container proof (optional; needs a usable Docker or Podman engine):

```bash
bash ./scripts/container-proof.sh     # prefers docker compose, else podman compose
python3 -m json.tool .container-proof/receipt.json
```

This proof picks free loopback ports, runs its own isolated Compose project, and
**must tear that project down before it can pass**. It does not leave services
listening on 8080, and a pass is attested only by `.container-proof/receipt.json`
(or a successful CI `container-proof` job that produced one). Do not point Hermes
or MCP smokes at 8080 afterward and expect that stack to still be up.

Manual Compose + Hermes workflow (separate; you keep the stack running):

```bash
# podman machine start   # if using Podman
podman compose up -d --build          # or: docker compose up -d --build
# optional older smoke path:
# ./scripts/smoke.sh
ENTERPRISE_API_URL=http://127.0.0.1:${ENTERPRISE_API_PORT:-8080} ./scripts/mcp-smoke.sh
ENTERPRISE_API_URL=http://127.0.0.1:${ENTERPRISE_API_PORT:-8080} ./scripts/hermes-tool-filter-proof.sh
```

`workflow-runner` runs to completion and exits 0 by design. Some compose
providers treat that as a failure under `--wait`; the API service is the one that
must be healthy.

## Health checks

| Endpoint | Meaning |
|---|---|
| `GET /healthz` | Process is alive |
| `GET /readyz` | Service finished startup and can serve traffic |
| `GET /metrics` | Intentionally unauthenticated Prometheus exposition; request and action-outcome series. Compose publishes the API on loopback only |

## Scripts

| Command | Purpose |
|---|---|
| `./scripts/demo.sh` | Full flow; starts its own API only when `ENTERPRISE_API_URL` is unset. An explicit URL must be loopback and healthy or the demo fails closed |
| `./scripts/telemetry-proof.sh` | Checksum-verified native Prometheus: rule tests, live scrape/query, receipt, cleanup; no containers |
| `./scripts/trace-proof.sh` | Loopback OTLP/HTTP capture of W3C CLIENT/SERVER spans and bounded approval/failure/resume events; receipt under `.trace-proof/` |
| `bash ./scripts/container-proof.sh` | Isolated Compose API + Prometheus proof (dynamic ports, required teardown); writes `.container-proof/receipt.json` on pass |
| `./scripts/proof.sh` | Canonical local proof (pytest, inspect, compose parse, demo, native telemetry, native traces). Opt in to containers with `PROOF_WITH_CONTAINERS=1` or `--with-containers` |
| `./scripts/smoke.sh` | Containerized workflow-runner receipt |
| `./scripts/mcp-smoke.sh` | FastMCP protocol check (explicit credentials plus a wrong-token check) and Hermes discovery |
| `MCP_SMOKE_PROTOCOL_ONLY=1 ./scripts/mcp-smoke.sh` | Protocol check only (CI, no Hermes CLI) |
| `./scripts/hermes-mcp-proof.sh` | Check Hermes discovery in an isolated home directory |
| `./scripts/hermes-tool-filter-proof.sh` | Change the server allowlist and show Hermes seeing 4 tools, then 1 |
| `./scripts/fresh-clone-check.sh` | Clone HEAD to a temp dir, fresh venv, full suite + demo; does not run native telemetry, trace, Hermes, smoke, or container checks |
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

## Telemetry alerts

The API exports route-template request counters/latency histograms and mutation
outcomes. Prometheus evaluates these operator-facing rules:

| Alert | Meaning | First response |
|---|---|---|
| `EnterpriseApiAvailabilityFastBurn` | 99% availability budget burning rapidly across 1h and 5m windows | Check target health and recent API 5xx logs; stop automated retries until the failure mode is known |
| `EnterpriseApiAvailabilitySlowBurn` | Sustained availability burn across 6h and 30m windows | Open a tracked investigation and compare error classes with expected fixture traffic |
| `EnterpriseApiLatencyFastBurn` | More than the allowed share of requests exceed 500 ms across 1h and 5m windows | Check API saturation and injected timeout settings; contain traffic before retrying writes |
| `EnterpriseApiLatencySlowBurn` | Sustained latency-budget burn across 6h and 30m windows | Review route-level latency and create a capacity/performance ticket |
| `EnterpriseApiPostCommitFailure` | A mutation committed and then returned 5xx | **Do not mint a new key.** Resume with the same approval capability and idempotency key, then confirm `replayed` and one record |

Validate the complete local telemetry path with:

```bash
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/telemetry-proof.sh
python3 -m json.tool .telemetry-proof/receipt.json
```

The proof must report target `up`, five loaded alerts, positive firing fixtures
for all five, the idle-latency negative control, and `created`, `replayed`,
`conflict`, and `postcommit_error` outcomes. It chooses temporary native ports and removes its
processes. Compose publishes Prometheus on `${PROMETHEUS_PORT:-9090}`;
`container-proof.sh` chooses a free host port automatically.

No Alertmanager or pager is configured. A loaded/firing rule is **not** proof of
external notification delivery. This is Prometheus metrics evidence. Distributed
traces are a separate loopback OTLP proof; see [slo.md](slo.md) for the 99%
availability and 95% under-500ms objectives, burn-alert arithmetic, and
forbidden trace attributes.

```bash
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/trace-proof.sh
python3 -m json.tool .trace-proof/receipt.json
```

The trace proof must report direct workflow CLIENT→API SERVER causality: the
SERVER span shares the W3C trace ID and has `parent_span_id == client.span_id`.
It must also report the bounded `approval.requested` /
`mutation.failed_resumable` / `mutation.replayed` events. It must not contain
fixture tokens, capabilities, idempotency keys, notes, or bodies. Capture binds
127.0.0.1 only and is not a collector backend.

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

`ENTERPRISE_API_URL` must point at the published Compose host port
(`http://127.0.0.1:${ENTERPRISE_API_PORT:-8080}` for manual Compose use).
Inside containers use `http://enterprise-api:8080`. `container-proof.sh`
overrides the host port with a free loopback port and passes that exact URL to
the demo; do not assume it used 8080.

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

This endpoint works only after the API has successfully loaded its configured
store:

```bash
AUTHORIZATION_HEADER="Authorization: Bearer <write-token>" # gitleaks:allow -- documented placeholder
curl -X POST -H "$AUTHORIZATION_HEADER" \
  http://127.0.0.1:8080/v1/admin/reset-actions
```

### API will not start because `ACTION_STORE_PATH` is invalid

The store intentionally fails closed when its JSON is malformed, has invalid
record fields, repeats an idempotency key, or contains more than one record for
an incident/action pair. Because the store loads during module import, the API
and `/v1/admin/reset-actions` are unavailable in this state.

1. Stop the API and preserve a copy of the file named by `ACTION_STORE_PATH` as
   evidence. Do not edit the only copy.
2. Inspect and reconcile the duplicate/corrupt fixture records offline, or move
   the invalid file aside if discarding fixture state is explicitly acceptable.
3. Start the API and confirm `/readyz` before issuing any mutation.
4. Re-run the persistence and aggregate proof gates.

The file-backed store reads and validates the complete JSON under a single-host
lock on each operation. This is acceptable for the small fixture and is not a
high-volume recovery or production persistence mechanism.

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
| `.telemetry-proof/receipt.json` | Native Prometheus release hash, target, alerts, rule fixtures, and queried metric outcomes |
| `.trace-proof/receipt.json` | Native OTLP capture: verified CLIENT→SERVER parent links, shared trace IDs, span names, bounded approval events |
| `.container-proof/receipt.json` | Compose restart/replay and telemetry evidence when a container runtime pass exists |
| `.audit/*.jsonl` | Append-only audit trail |

All are gitignored.

## Security notes

- Do not commit `.env`.
- Do not embed tokens in committed YAML; use `${ENTERPRISE_API_TOKEN}`.
- Do not merge lab MCP config into live `~/.hermes/config.yaml`.
- Receipts and audit logs are local proof only; scrub before sharing.
