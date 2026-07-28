# Hermes Enterprise Deployment Lab

A customer-shaped local integration lab with a real FastMCP stdio surface that Hermes Agent can discover; tool invocation is proven at the FastMCP protocol level. Includes explicit approval metadata in plan receipts, failure receipts, and reproducible Podman-based operation.

> Status: **M3 Partial (Yellow)** — local FastMCP protocol proof is green; GitHub Actions CI is configured (pending first run); full M3 requires local Hermes CLI discovery proof. Not production Hermes Enterprise deployment, cloud/hybrid scale, OIDC, Kubernetes, or production ML engineering.

## What this proves

| Layer | Proof |
|---|---|
| **Compose lab** | `enterprise-api` + `workflow-runner` run as containerized local components |
| **FastMCP protocol** | Three read/plan tools callable via `fastmcp` CLI and unit tests |
| **Hermes discovery** | Isolated `HERMES_HOME` + `hermes mcp test` lists `mcp__enterprise_ops__*` tools (local only) |
| **Workflow seam** | Bearer auth, correlation IDs, approval metadata in receipts |

**Hermes Agent is an external operator/client** — it is not a Compose service. Proof uses an isolated `HERMES_HOME`, never your live `~/.hermes/config.yaml`.

## Architecture (summary)

```
Hermes Agent (external) ──stdio──► enterprise-mcp ──► enterprise-api ──► fixtures
                                        │
                                 JSON receipt (approval_required=true)
workflow-runner (Compose) ──────────────┘
```

See [`docs/architecture.md`](docs/architecture.md) for component boundaries and [`docs/runbook.md`](docs/runbook.md) for troubleshooting.

## Fresh-clone setup

Prerequisites: **Podman 4+** with Compose, **Python 3.11+**, and **Hermes CLI** for full smoke (protocol-only CI skips Hermes).

```bash
git clone <repo-url> hermes-enterprise-deployment-lab
cd hermes-enterprise-deployment-lab
cp .env.example .env

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r workflow-runner/requirements.txt -r enterprise-mcp/requirements.txt
.venv/bin/python -m pytest -q

podman machine start   # once, if the default machine is stopped
podman compose up -d --build --wait
./scripts/smoke.sh
./scripts/mcp-smoke.sh          # requires hermes CLI
./scripts/hermes-mcp-proof.sh   # discovery-only Hermes receipt
podman compose down -v
```

Docker may work with the same images but is **unverified** in this lab; Podman is the supported runtime.

### Protocol-only smoke (CI mode)

```bash
export MCP_SMOKE_PROTOCOL_ONLY=1
./scripts/mcp-smoke.sh
```

This runs FastMCP inspect/list/call proof without Hermes CLI. Full local smoke **requires** Hermes and fails closed when it is absent.

## Expected smoke output

Workflow smoke (`.smoke-receipts/workflow-receipt.json`):

```json
{
  "smoke": "passed",
  "receipt_path": "<repo>/.smoke-receipts/workflow-receipt.json",
  "correlation_id": "<uuid>"
}
```

MCP smoke (`.mcp-receipts/mcp-smoke-receipt.json`) separates proof layers:

```json
{
  "overall_status": "passed",
  "fastmcp_protocol": { "status": "passed", "...": "..." },
  "hermes_discovery": {
    "status": "passed",
    "invocation": "discovery_only",
    "discovered_tools": [
      "mcp__enterprise_ops__check_enterprise_api",
      "mcp__enterprise_ops__get_incident_context",
      "mcp__enterprise_ops__propose_incident_plan"
    ]
  }
}
```

Tool **invocation** is proven at the FastMCP protocol layer. Hermes CLI `mcp test` proves **discovery only** (no LLM/provider call).

### Hermes MCP config (isolated)

Never copy into `~/.hermes/config.yaml`. Generate a lab config:

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

Example shape: [`config/hermes-mcp-example.yaml`](config/hermes-mcp-example.yaml). Token uses `${ENTERPRISE_API_TOKEN}` — no literal secrets in git.

## Fixture token

Local lab only — set in `.env` or your shell, **not** in committed config:

```
ENTERPRISE_API_TOKEN=lab-read-token
```

## Limitations

- Customer-shaped lab, not a production customer deployment
- Single deterministic incident fixture (`INC-2026-0042`)
- MCP tools are read/plan only; no external mutation
- Approval is **represented** in plan receipts; there is no runtime human-approval gate enforcing mutations
- No real cloud identity, CRM, or data-warehouse integrations
- Agent orchestration / LLM reasoning is out of scope for M3 proof

## Milestones

| Milestone | Status |
|---|---|
| M1 Green local deployment | Green |
| M2 Identity/integration boundary | **Partial/Yellow** — static bearer auth only; no connector pagination/retry per build-spec |
| M3 Agent workflow (MCP + Hermes discovery) | **Partial/Yellow** — protocol green; Hermes discovery local |
| M4 Observability + failure scripts | Planned |

Controlling spec: [`docs/build-spec.md`](docs/build-spec.md)

## License

MIT — see [LICENSE](LICENSE). Security notes: [SECURITY.md](SECURITY.md).
