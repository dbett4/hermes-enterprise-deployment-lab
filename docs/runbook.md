# Operator Runbook

## Quick commands

```bash
cp .env.example .env
export ENTERPRISE_API_TOKEN=lab-read-token
podman compose up -d --build --wait
./scripts/smoke.sh
./scripts/mcp-smoke.sh
./scripts/hermes-mcp-proof.sh
podman compose down -v
```

## Health checks

| Endpoint | Meaning |
|---|---|
| `GET /healthz` | Process is alive |
| `GET /readyz` | Service finished startup and can serve traffic |

Compose waits on `enterprise-api` health before smoke uses the stack.

## MCP discovery and smoke

| Command | Purpose |
|---|---|
| `fastmcp inspect enterprise-mcp/enterprise_mcp/server.py:mcp` | Summarize MCP server capabilities |
| `fastmcp list --command ./scripts/run-enterprise-mcp.sh --json` | List the three tools |
| `./scripts/mcp-smoke.sh` | FastMCP protocol calls + Hermes discovery (default) |
| `MCP_SMOKE_PROTOCOL_ONLY=1 ./scripts/mcp-smoke.sh` | Protocol proof only (CI) |
| `./scripts/hermes-mcp-proof.sh` | Isolated Hermes discovery receipt |
| `./scripts/emit-hermes-mcp-config.sh` | Emit Hermes config with absolute paths |

### Hermes MCP test (isolated)

```bash
export ENTERPRISE_API_TOKEN=lab-read-token
export HERMES_HOME=/tmp/hermes-mcp-lab
mkdir -p "$HERMES_HOME"
{
  echo "_config_version: 9"
  ./scripts/emit-hermes-mcp-config.sh
} > "$HERMES_HOME/config.yaml"
hermes mcp test enterprise_ops
```

Expected prefixed tools: `mcp__enterprise_ops__check_enterprise_api`, `mcp__enterprise_ops__get_incident_context`, `mcp__enterprise_ops__propose_incident_plan`.

Hermes `mcp test` proves discovery only. Tool invocation is proven via FastMCP in `mcp-smoke.sh`.

## Troubleshooting

### Smoke fails with connection refused on port 8080

1. `podman compose ps`
2. `podman compose logs enterprise-api`
3. Ensure the Podman machine is running: `podman machine start`

### MCP smoke fails before FastMCP calls

Ensure `ENTERPRISE_API_URL` points at the published Compose port (`http://127.0.0.1:8080` from the host). Inside containers use `http://enterprise-api:8080`.

### MCP smoke fails: hermes CLI not found

Full smoke requires Hermes CLI on PATH. For protocol-only proof: `MCP_SMOKE_PROTOCOL_ONLY=1 ./scripts/mcp-smoke.sh`.

### Hermes MCP discovery fails

1. Export `ENTERPRISE_API_TOKEN` before `hermes mcp test` (config uses `${ENTERPRISE_API_TOKEN}`).
2. Confirm `PYTHONPATH` includes `<repo>/enterprise-mcp` and `<repo>/workflow-runner` in the server `env` block.
3. Run `fastmcp list --command ./scripts/run-enterprise-mcp.sh --json` with the same `PYTHONPATH`.
4. Check `hermes mcp test enterprise_ops` against an isolated `HERMES_HOME`, not `~/.hermes`.

### Missing MCP dependency (`ModuleNotFoundError: workflow_runner`)

Set `PYTHONPATH` in the MCP server env or install deps: `pip install -r requirements-dev.txt -r workflow-runner/requirements.txt -r enterprise-mcp/requirements.txt`.

### `401 Missing bearer token`

The workflow runner, MCP server, or curl call did not send an `Authorization: Bearer …` header. Confirm `.env` contains `ENTERPRISE_API_TOKEN=lab-read-token` or export it in the shell.

### `403 Invalid or insufficient token scope` / `auth_failure` receipt

Token mismatch between client and API. Recovery:

```bash
export ENTERPRISE_API_TOKEN=lab-read-token
podman compose up -d --wait
./scripts/mcp-smoke.sh
```

### `404 Incident not found`

Use the fixture incident `INC-2026-0042` or extend fixtures in `enterprise-api/app/fixtures.py`.

### `malformed_response` receipt

Upstream returned non-JSON. Recovery:

```bash
podman compose logs enterprise-api
podman compose restart enterprise-api
./scripts/mcp-smoke.sh
```

### Injected `500` during drill

Expected when calling with `?inject=error` or `X-Inject-Failure: error`. Remove the injection parameter to recover.

### Injected timeout during drill

Expected when calling with `?inject=timeout`. Default sleep is `INJECT_TIMEOUT_SECONDS` (2s in Compose). Reduce locally for faster drills.

### Workflow or MCP receipt shows `outcome: error`

Inspect `error.code` in the receipt or tool payload:

| code | Meaning | Recovery |
|---|---|---|
| `auth_failure` | Token rejected | `export ENTERPRISE_API_TOKEN=lab-read-token` and re-run smoke |
| `not_found` | Incident/runbook missing | Use valid fixture ID |
| `timeout` | Upstream too slow | Check API health, reduce injection sleep |
| `malformed_response` | Non-JSON or wrong shape | `podman compose restart enterprise-api` |
| `upstream_5xx` | API error | Check API logs, clear failure injection |

### Correlation ID inspection

- API responses include `X-Correlation-ID`.
- Workflow receipts and MCP tool payloads include run-level `correlation_id`.
- Each `dependency_calls[]` entry has its own `correlation_id` when returned by the API.

## Receipt locations

| Path | Contents |
|---|---|
| `.smoke-receipts/workflow-receipt.json` | Workflow runner smoke receipt |
| `.mcp-receipts/mcp-smoke-receipt.json` | FastMCP protocol + Hermes discovery proof |
| `.mcp-receipts/hermes-mcp-proof.json` | Hermes-only discovery receipt |

All directories are gitignored.

## Security notes

- Do not commit `.env`.
- Do not embed tokens in committed YAML; use `${ENTERPRISE_API_TOKEN}`.
- Do not merge lab MCP config into live `~/.hermes/config.yaml`.
- Receipts are for local proof only; scrub before sharing externally.
