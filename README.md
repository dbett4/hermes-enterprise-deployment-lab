# Hermes Enterprise Deployment Lab

A customer-shaped reference deployment for integrating and operating Hermes Agent with an authenticated internal API, explicit approval boundaries, observable execution, and reproducible failure recovery.

> Status: **M1 local deployment scaffold** — not public, no client data, no real Hermes credentials.

## What this proves

- Containerized mock enterprise API with health/readiness, scoped bearer auth, correlation IDs, and failure injection
- Workflow integration seam that retrieves incident + runbook data and emits an approval-gated action plan receipt
- Contract tests, Compose orchestration, and a deterministic smoke workflow suitable for a 2 GB Podman VM

## Architecture (summary)

```
operator → scripts/smoke.sh → workflow-runner → enterprise-api → fixtures
                                      ↓
                               JSON receipt (approval_required=true)
```

See [`docs/architecture.md`](docs/architecture.md) for threat boundaries and [`docs/runbook.md`](docs/runbook.md) for troubleshooting.

## Five-minute quickstart

Prerequisites: Podman 4+ with Compose, Python 3.11+.

```bash
cp .env.example .env
python3 -m pip install -r requirements-dev.txt -r workflow-runner/requirements.txt
python3 -m pytest -q

podman machine start   # once, if the default machine is stopped
podman compose up -d --build --wait
./scripts/smoke.sh
podman compose down -v
```

Expected smoke output (abridged):

```json
{
  "smoke": "passed",
  "receipt_path": "<repo>/.smoke-receipts/workflow-receipt.json",
  "correlation_id": "<uuid>"
}
```

The receipt includes `approval_required: true`, two dependency calls, and proposed actions derived from the fixture runbook.

## Fixture token

Local lab only — **not** a production pattern:

```
ENTERPRISE_API_TOKEN=lab-read-token
```

## Limitations

- Customer-shaped lab, not a production customer deployment
- Not yet the Hermes Agent container, plugin, or MCP surface (M3)
- Single deterministic incident fixture (`INC-2026-0042`)
- No real cloud identity, CRM, or data-warehouse integrations

## Milestones

| Milestone | Status |
|---|---|
| M1 Green local deployment | In progress (this branch) |
| M2 Identity/integration boundary | Partial (scoped token + error classes) |
| M3 Agent workflow | Planned |
| M4 Observability + failure scripts | Planned |

Controlling spec: [`docs/build-spec.md`](docs/build-spec.md)
