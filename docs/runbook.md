# Operator Runbook

## Quick commands

```bash
cp .env.example .env
podman compose up -d --build --wait
./scripts/smoke.sh
podman compose down -v
```

## Health checks

| Endpoint | Meaning |
|---|---|
| `GET /healthz` | Process is alive |
| `GET /readyz` | Service finished startup and can serve traffic |

Compose waits on `enterprise-api` health before smoke uses the stack.

## Troubleshooting

### Smoke fails with connection refused on port 8080

1. `podman compose ps`
2. `podman compose logs enterprise-api`
3. Ensure the Podman machine is running: `podman machine start`

### `401 Missing bearer token`

The workflow runner or curl call did not send an `Authorization: Bearer …` header. Confirm `.env` contains `ENTERPRISE_API_TOKEN=lab-read-token` or export it in the shell.

### `403 Invalid or insufficient token scope`

Token mismatch between workflow runner and API. Align `ENTERPRISE_API_TOKEN` in `.env` and Compose environment.

### `404 Incident not found`

Use the fixture incident `INC-2026-0042` or extend fixtures in `enterprise-api/app/fixtures.py`.

### Injected `500` during drill

Expected when calling with `?inject=error` or `X-Inject-Failure: error`. Remove the injection parameter to recover.

### Injected timeout during drill

Expected when calling with `?inject=timeout`. Default sleep is `INJECT_TIMEOUT_SECONDS` (2s in Compose). Reduce locally for faster drills.

### Workflow receipt shows `outcome: error`

Inspect `error.code` in the receipt:

| code | Meaning | Recovery |
|---|---|---|
| `auth_failure` | Token rejected | Fix `ENTERPRISE_API_TOKEN` |
| `not_found` | Incident/runbook missing | Use valid fixture ID |
| `timeout` | Upstream too slow | Check API health, reduce injection sleep |
| `malformed_response` | Non-JSON or wrong shape | Check API logs |
| `upstream_5xx` | API error | Check API logs, clear failure injection |

## Receipt location

Smoke writes `.smoke-receipts/workflow-receipt.json` in the repository via a bind mount. The directory is gitignored.

## Security notes

- Do not commit `.env`.
- Do not treat `lab-read-token` as a production pattern.
- Receipts are for local proof only; scrub before sharing externally.
