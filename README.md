# Hermes Enterprise Deployment Lab

A local, customer-shaped integration lab: a mock enterprise API, a workflow seam,
and a FastMCP stdio server that the Hermes CLI really connects to and lists tools
from. The interesting part is not the plumbing — it is that a consequential
action **stops for human approval**, survives a **forced mid-flight failure**,
and **resumes without applying twice**, with an append-only audit trail.

Run the whole thing in one command:

```bash
./scripts/demo.sh
```

> Status: **local proof, no CI run, no second operator.** See
> [What this does not prove](#what-this-does-not-prove) — it is deliberately
> specific, and it is the part worth reading first.

## What this proves

Each row names the command that establishes it. If a row has no command, it does
not belong in this table.

| Claim | Established by |
|---|---|
| Hermes Agent connects to this MCP server over stdio and lists its tools | `./scripts/hermes-mcp-proof.sh` — real `hermes` CLI, isolated `HERMES_HOME` |
| The tool surface Hermes sees is scoped, and changing the scope changes what Hermes sees | `./scripts/hermes-tool-filter-proof.sh` — differential: 4 tools vs 1 |
| An excluded tool is neither listed nor callable | `pytest enterprise-mcp/tests/test_tool_filtering.py` |
| A mutating call **without** an approval token sends no write request at all | `pytest workflow-runner/tests/test_executor.py` (asserted against observed HTTP traffic) |
| A forged token, or a token bound to another action, is refused | `pytest enterprise-mcp/tests/test_approval_and_resume_over_mcp.py` |
| A fault injected **after commit** is survivable: the resume replays instead of re-applying | same file, `test_forced_failure_then_resume_leaves_one_side_effect` |
| Exactly one side effect exists after failure + resume | same test, and `./scripts/demo.sh` STEP 7 |
| A read-only credential cannot mutate | `pytest enterprise-api/tests/test_actions.py` + demo STEP 8 |
| Credentials really reach the MCP subprocess — a wrong token actually fails | `pytest enterprise-mcp/tests/test_stdio_credential_injection.py` |
| The server fails closed with no token instead of falling back to a default | same file, `test_server_fails_closed_without_a_token` |
| Every step is recorded append-only with correlation IDs | `pytest workflow-runner/tests/test_audit.py`, demo STEP 9 |
| A clean clone of HEAD reproduces all of the above | `./scripts/fresh-clone-check.sh` |

**Hermes Agent is an external operator/client**, not a Compose service. Proofs use
an isolated `HERMES_HOME`; they never read or write your live `~/.hermes/config.yaml`.

## What this does not prove

- **No LLM has ever chosen or invoked these tools.** Every tool call in this
  repository is made by a script or a test. `hermes mcp test` performs discovery
  only; it makes no provider call. Closing this needs a model-driven run
  (`hermes -z` with the toolset narrowed to `enterprise_ops`) plus the session
  transcript showing the tool-call event. That spends provider credits and is a
  deliberate, separate decision — it has not been done.
- **Hermes-side tool filtering is not proven.** Hermes has a `tools.include`
  list, and on 2026-08-01 narrowing it to a single entry still made
  `hermes mcp test` print all three tools, because that command reports the
  server's advertised surface. The scoping proven here is enforced by *this
  server*, not by Hermes.
- **CI has never run.** There is no git remote for this repository, so
  `.github/workflows/ci.yml` has never executed. The fresh-clone half is proven
  locally by `scripts/fresh-clone-check.sh`; the green-badge half is not.
- **No second operator has validated any of this.** The protocol for doing so is
  [`docs/second-operator-protocol.md`](docs/second-operator-protocol.md), with an
  empty results section.
- **The approval gate is a workflow-layer control, not a platform one.** A
  different client holding the write token could call the enterprise API directly
  and bypass it.
- **Not a production deployment.** No OIDC, no Kubernetes, no real identity
  provider, no cloud/hybrid scale, no real customer data. One deterministic
  incident fixture (`INC-2026-0042`). The "mutation" is a record in an in-memory
  store inside the lab API.
- **Docker is unverified.** Podman is the supported container runtime here.

## The arc

```
                    approval token (the human step)
                              │
Hermes / script ──stdio──► enterprise-mcp ──Bearer+Idempotency-Key──► enterprise-api
                              │                                            │
                              └──────────► append-only audit log ◄─────────┘
                                           (.audit/*.jsonl)
```

1. **Scoped discovery** — the default allowlist exposes read/plan tools only.
2. **Read + plan** — `propose_incident_plan` returns runbook steps with stable
   `action_id`s and `approval_required` flags.
3. **Approval stop** — `apply_incident_plan` without a token writes nothing and
   returns `pending_approval` plus a token and an idempotency key.
4. **Forced failure** — with `ENTERPRISE_INJECT_FAILURE=error_after_commit` the
   API commits the record and *then* returns 500. The caller sees `upstream_5xx`
   and resume instructions.
5. **Resume** — the same approval token replays the same idempotency key; the API
   returns the original record with `replayed: true`.
6. **Exactly once** — the store holds one record.

## Fresh-clone setup

Prerequisites: **Python 3.11, 3.12, or 3.13**. Not 3.14 — `pydantic-core` has no
wheel for it and its vendored PyO3 tops out at 3.13, so a source build fails.
Podman and the Hermes CLI are optional and only needed for the container and
Hermes proofs.

```bash
git clone <repo-url> hermes-enterprise-deployment-lab
cd hermes-enterprise-deployment-lab
cp .env.example .env

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt \
                      -r workflow-runner/requirements.txt \
                      -r enterprise-mcp/requirements.txt
.venv/bin/python -m pytest -q

./scripts/demo.sh          # the whole arc; boots its own API, no containers needed
```

Container and Hermes proofs:

```bash
podman machine start                 # once, if the default machine is stopped
podman compose up -d --build
./scripts/smoke.sh                   # containerized workflow-runner receipt
ENTERPRISE_API_URL=http://127.0.0.1:8080 ./scripts/mcp-smoke.sh
ENTERPRISE_API_URL=http://127.0.0.1:8080 ./scripts/hermes-tool-filter-proof.sh
podman compose down -v
```

`workflow-runner` is a run-to-completion container that exits 0 by design; some
compose providers report that as a failure under `--wait`.

### Protocol-only smoke (CI mode)

```bash
MCP_SMOKE_PROTOCOL_ONLY=1 ./scripts/mcp-smoke.sh
```

Runs the FastMCP inspect/list/call proof without the Hermes CLI. Full local smoke
**requires** Hermes and fails closed when it is absent.

## Configuration

| Variable | Meaning |
|---|---|
| `ENTERPRISE_API_TOKEN` | Read scope. **Required** — there is no default; the server exits 2 without it |
| `ENTERPRISE_API_WRITE_TOKEN` | Write scope. Absent means the server cannot mutate |
| `ENTERPRISE_MCP_ENABLED_TOOLS` | Tool allowlist. Unset = read/plan only; `all` = also expose `apply_incident_plan` |
| `ENTERPRISE_INJECT_FAILURE` | Deterministic fault: `error`, `error_after_commit`, `timeout` |
| `AUDIT_LOG_PATH` / `APPROVAL_STORE_PATH` | Where the audit trail and approval store live |

**MCP stdio does not inherit your environment.** The SDK forwards only
`HOME`, `LOGNAME`, `PATH`, `SHELL`, `USER`. Anything else must be passed
explicitly via `env=` on the stdio transport or the Hermes `env:` block. This
repository got that wrong once and the failure was silent — see
[ADR 004](docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md).

### Hermes MCP config (isolated)

Never merge this into `~/.hermes/config.yaml`.

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

The second argument is the server-side allowlist. Example shape:
[`config/hermes-mcp-example.yaml`](config/hermes-mcp-example.yaml).

## Fixture tokens

Local lab only — non-secret test data, documented in `.env.example`:

```
ENTERPRISE_API_TOKEN=lab-read-token
ENTERPRISE_API_WRITE_TOKEN=lab-write-token
```

## MCP tool surface

| Tool | Mutating | Behavior |
|---|---|---|
| `check_enterprise_api` | no | Health/readiness, correlation ID, whether a write credential is present |
| `get_incident_context` | no | Incident + runbook with per-dependency call evidence |
| `propose_incident_plan` | no | Plan receipt; consequential steps carry `approval_required` and an `action_id` |
| `apply_incident_plan` | **yes** | Approval-gated, idempotent execution of one runbook step. Opt-in via the allowlist |

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Components, proof layers, threat boundary |
| [`docs/runbook.md`](docs/runbook.md) | Operator commands and troubleshooting |
| [`docs/adr/003-stdio-mcp-read-plan-tools.md`](docs/adr/003-stdio-mcp-read-plan-tools.md) | Why stdio MCP |
| [`docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md`](docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md) | Enforced approval, idempotency, scoped tools, the credential defect |
| [`docs/second-operator-protocol.md`](docs/second-operator-protocol.md) | Validation script for someone else — results section empty |
| [`docs/build-spec.md`](docs/build-spec.md) | Controlling spec |

## Milestones

| Milestone | Status |
|---|---|
| M1 Green local deployment | Green |
| M2 Identity/integration boundary | **Partial** — two static bearer scopes; no OIDC, no connector pagination/retry |
| M3 Agent workflow (MCP + Hermes discovery) | **Partial** — discovery and scoping proven with the real Hermes CLI; model-driven invocation not attempted |
| M4 Approval, idempotency, resume, audit | Green locally — see the proof table |
| M5 CI green from a fresh clone | **Red** — no remote; the workflow has never run |

## License

MIT — see [LICENSE](LICENSE). Security notes: [SECURITY.md](SECURITY.md).
