# Hermes Enterprise Deployment Lab

An MCP surface that a real Hermes Agent build discovers and enumerates, with
enforced tool scoping, fail-closed credential handling, separated operator
approval, and exactly-once resume proven after a forced failure. Locally, in a
customer-shaped lab: a mock enterprise API, a workflow seam, a FastMCP stdio
server, and an operator-only approval command.

That is the whole claim. Two things it is specifically **not**:

- **The approval identity is a lab assertion, not enterprise authentication.**
  The MCP caller gets only an opaque approval ID. A separate command records an
  operator identity and releases a time-bound capability whose plaintext is
  never stored. The demo automates that operator role with a fixture identity;
  it does not prove a real person's identity or judgment.
- **No model has ever invoked these tools, and none will.** Every call here comes
  from a script or a test. Hermes's real involvement is discovery and
  enumeration, which is proven; model-driven invocation is not, and it is not
  coming.

Both are expanded in [What this does not prove](#what-this-does-not-prove).

Run the whole thing in one command:

```bash
./scripts/demo.sh
```

Run the complete credential-free proof packet with `./scripts/proof.sh`. It
runs all 73 tests, inspects the MCP server, parses the Compose topology, and
executes the failure/resume demo. See [PROOF.md](PROOF.md) for exact claim
boundaries.

> Status: **local proof only — no CI run, no independent second-operator
> validation, no model invocation, no production identity provider.** See
> [What this does not prove](#what-this-does-not-prove) — it is deliberately
> specific, and it is the part worth reading first.

## What this proves

Each row names the command that establishes it. If a row has no command, it does
not belong in this table.

| Claim | Established by |
|---|---|
| A real Hermes Agent build connects to this MCP server over stdio and enumerates its tools (discovery only — Hermes does not invoke anything here) | `./scripts/hermes-mcp-proof.sh` — real `hermes` CLI, isolated `HERMES_HOME` |
| The tool surface Hermes sees is scoped, and changing the scope changes what Hermes sees | `./scripts/hermes-tool-filter-proof.sh` — differential: 4 tools vs 1 |
| An excluded tool is neither listed nor callable | `pytest enterprise-mcp/tests/test_tool_filtering.py` |
| A mutation request returns only an opaque `approval_id`; it leaks neither the capability nor idempotency key and sends no write request | `pytest workflow-runner/tests/test_executor.py` (asserted against observed HTTP traffic) |
| Only the separate operator path can approve; approver identity is recorded and the plaintext capability is not stored | same file, `test_operator_identity_is_recorded_and_plaintext_capability_is_not` |
| A forged, expired, already-applied, or wrongly bound capability is refused before dispatch | workflow-runner unit tests + MCP end-to-end tests |
| A fault injected **after commit** is survivable: the resume replays instead of re-applying | same file, `test_forced_failure_then_resume_leaves_one_side_effect` |
| Exactly one side effect exists after failure + resume, then the capability becomes terminal | same test, and `./scripts/demo.sh` STEP 8 |
| A read-only credential cannot mutate | `pytest enterprise-api/tests/test_actions.py` + demo STEP 9 |
| Credentials really reach the MCP subprocess — a wrong token actually fails | `pytest enterprise-mcp/tests/test_stdio_credential_injection.py` |
| The server fails closed with no token instead of falling back to a default | same file, `test_server_fails_closed_without_a_token` |
| The workflow runner's audit log records request, named operator grant, capability acceptance, failure, and replay | `pytest workflow-runner/tests/test_audit.py`, demo STEP 10. The log is not tamper-evident |
| A clean clone of HEAD reproduces the test suite and the demo | `./scripts/fresh-clone-check.sh` — it runs `pytest` and `scripts/demo.sh` only; it does **not** re-run the Hermes or container proofs |

**Hermes Agent is an external operator/client**, not a Compose service. Proofs use
an isolated `HERMES_HOME`; they never read or write your live `~/.hermes/config.yaml`.
Hermes's role in this repository is discovery and enumeration. It never invokes a
tool here, and no output in this repository was produced by Hermes acting on its
own — the callers are `scripts/*.sh` and `pytest`.

## What this does not prove

- **No LLM has ever chosen or invoked these tools, and none will.** Every tool
  call in this repository is made by a script or a test. `hermes mcp test`
  performs discovery only; it makes no provider call. Closing this would need a
  model-driven run (`hermes -z` with the toolset narrowed to `enterprise_ops`)
  plus the session transcript showing the tool-call event. **The owner declined
  that provider spend on 2026-08-01, so it will not be done.** Treat this as a
  permanent ceiling on the repository, not an open task. What Hermes really did
  here — and all it did — is discover and enumerate the tool surface over stdio.
  Nothing in this repository was "run by Hermes" in any other sense.
- **The demo does not prove human identity or judgment.** The role boundary is
  real in code: the MCP surface cannot grant approval, and its request response
  contains no capability. The demo then invokes the separate operator command
  with `demo-operator@example.com` so the entire arc remains deterministic. In a
  real deployment that command must sit behind authenticated operator access.
- **The approval store is an unauthenticated JSON file.** Anything that can
  write `APPROVAL_STORE_PATH` can still subvert the lab control. The store keeps
  only a SHA-256 capability hash, but it is not a transactional or authenticated
  production authorization service.
- **The audit log is append-only by convention, not tamper-evident.** It is a
  plain `.jsonl` file with no signature or chain hash; `run_started` and
  `run_finished` carry a null correlation ID; and the enterprise API writes no
  audit of its own, so a direct write to the API leaves no trace in it.
- **"Exactly once" is per approval, not per action.** Two approvals for the same
  `action_id` carry two idempotency keys and produce two records. Nothing
  deduplicates at the action level.
- **Hermes-side tool filtering is not proven.** Hermes has a `tools.include`
  list, and on 2026-08-01 narrowing it to a single entry still made
  `hermes mcp test` print all three tools, because that command reports the
  server's advertised surface. The scoping proven here is enforced by *this
  server*, not by Hermes.
- **CI has never run.** There is no git remote for this repository, so
  `.github/workflows/ci.yml` has never executed. The fresh-clone half is proven
  locally by `scripts/fresh-clone-check.sh`; the green-badge half is not.
- **No second operator has validated any of this, and none is scheduled.**
  [`docs/second-operator-protocol.md`](docs/second-operator-protocol.md) is an
  **unrun** protocol — a script nobody has executed — not pending evidence. Its
  results table is blank because the run has never happened.
- **The guard is a workflow-layer control, not a platform one.** A different
  client holding the write token could call the enterprise API directly and
  bypass it entirely, leaving no audit record.
- **Not a production deployment.** No OIDC, no Kubernetes, no real identity
  provider, no cloud/hybrid scale, no real customer data. One deterministic
  incident fixture (`INC-2026-0042`). The "mutation" is a record in an in-memory
  store inside the lab API.
- **Docker is unverified.** Podman is the supported container runtime here.

## The arc

```
Hermes / script ──stdio──► enterprise-mcp ──Bearer+Idempotency-Key──► enterprise-api
        │                     ▲       │
        │ approval_id         │       └──────────────► audit log
        ▼                     │
 approval store ◄── operator command --approver <identity>
        │                     │
        └─ one-time capability (plaintext never persisted)
```

1. **Scoped discovery** — the default allowlist exposes read/plan tools only.
2. **Read + plan** — `propose_incident_plan` returns runbook steps with stable
   `action_id`s and `approval_required` flags.
3. **Request blocked** — `apply_incident_plan` without a capability writes
   nothing and returns only `pending_approval` plus an opaque `approval_id`.
4. **Operator grant** — `python -m workflow_runner.approval_operator approve
   <approval_id> --approver <identity>` records the approver and returns the
   expiring capability once; only its hash is persisted.
5. **Forced failure** — with `ENTERPRISE_INJECT_FAILURE=error_after_commit` the
   API commits the record and *then* returns 500. The caller sees `upstream_5xx`
   and resume instructions.
6. **Resume** — the same capability replays the same idempotency key; the API
   returns the original record with `replayed: true`.
7. **Exactly once + terminal** — the store holds one record and another use of
   the applied capability is rejected without dispatch.

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
| `APPROVAL_TTL_SECONDS` | Lifetime of a pending/approved request; default 900 seconds |
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
| `apply_incident_plan` | **yes** | Requests or consumes a separately granted, expiring capability; idempotent execution of one runbook step. Opt-in via the allowlist |

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Components, proof layers, threat boundary |
| [`docs/runbook.md`](docs/runbook.md) | Operator commands and troubleshooting |
| [`docs/adr/003-stdio-mcp-read-plan-tools.md`](docs/adr/003-stdio-mcp-read-plan-tools.md) | Why stdio MCP |
| [`docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md`](docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md) | Historical two-call guard and credential/scoping decision |
| [`docs/adr/005-separated-operator-approval.md`](docs/adr/005-separated-operator-approval.md) | Superseding separated approval state machine and resume semantics |
| [`docs/second-operator-protocol.md`](docs/second-operator-protocol.md) | **Unrun** protocol — a script for a validator who does not exist yet, not pending evidence |
| [`docs/build-spec.md`](docs/build-spec.md) | Controlling spec |

## Milestones

| Milestone | Status |
|---|---|
| M1 Green local deployment | Green |
| M2 Identity/integration boundary | **Partial** — two static bearer scopes; no OIDC, no connector pagination/retry |
| M3 Agent workflow (MCP + Hermes discovery) | **Partial, permanently** — discovery and scoping proven with the real Hermes CLI; model-driven invocation will not be attempted (provider spend declined 2026-08-01) |
| M4 Separated approval, idempotency, resume, audit | **Partial** — role separation, expiry, terminal consumption, and ambiguous-failure resume are green locally. Identity is a caller-supplied fixture string and the audit is not tamper-evident |
| M5 CI green from a fresh clone | **Red** — no remote; the workflow has never run |

## Known limitations and roadmap

Everything here is a deliberate, recorded gap. Nothing in this list is in
progress.

| Item | State | Note |
|---|---|---|
| Production approval identity/policy integration | Not implemented | The operator command records a supplied identity but does not authenticate it. |
| Model-driven tool invocation | **Declined, 2026-08-01** | Provider spend declined. Permanent; this repository will never demonstrate it. |
| Second-operator validation | **Unrun** | `docs/second-operator-protocol.md` is a script nobody has executed. |
| CI run | **Never executed** | No git remote exists. |
| Action-level deduplication | Not implemented | "Exactly once" is per approval, not per action. |
| Approval consumption and expiry | Implemented locally | `pending → approved → applied` or `expired`; applied/expired are terminal. |
| Authenticated approval store | Not implemented | Plain JSON at `APPROVAL_STORE_PATH`; capability plaintext is not persisted. |
| Enterprise-API-side audit | Not implemented | A direct write to the API leaves no trace. |

### Approval boundary that remains

The local implementation now returns only an opaque request ID, grants through
a distinct operator command, records the supplied identity, hashes the stored
capability, enforces expiry/binding/terminal state, and preserves safe resume
after a post-commit ambiguity. It still does **not** authenticate that identity
or protect the JSON store from a local writer. Productionizing it would replace
the operator command and file store with an IdP-backed approval service and a
transactional audit store. Full text: [`docs/architecture.md`](docs/architecture.md).

## License

MIT — see [LICENSE](LICENSE). Security notes: [SECURITY.md](SECURITY.md).
