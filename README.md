# Hermes Enterprise Deployment Lab

> **Provenance.** Sanitized public extract published August 2026. Public git history
> is publication history, not the original private development timeline. Incident
> `INC-2026-0042`, operators, and API data are fictional fixtures. No client data or
> credentials appear in this repository. Private client history remains confidential;
> public claims are limited to inspectable artifacts.

## History and scope

I built this lab organically in **July–August 2026**. Early August work covered Hermes
MCP discovery, scoped tool surfaces, and a credential-injection regression
(2026-08-01). Provider spend for model-driven invocation was declined that day, so
every tool call in the repository comes from scripts or tests—not from a model. The
separated operator-approval design landed 2026-08-11 ([ADR 005](docs/adr/005-separated-operator-approval.md)).
The public extract was published later in August; GitHub dates mark publication, not a
longer private timeline.

This is a **synthetic lab**: mock enterprise API, fixture bearer tokens, in-memory
writes, and **73** credential-free tests. It is not evidence of customer-environment
deployment, production identity integration, or model-driven agent runs.

I built this small deployment lab to work through the failure cases that matter
when an agent can touch an internal system. It includes a mock enterprise API,
a FastMCP stdio server, a workflow runner, and a separate operator command for
approvals.

A real Hermes CLI build connects to the server and lists its tools. The server
decides which tools are available, refuses to start without credentials, and
keeps the write path separate from the approval path. The demo then forces an
error after the API has committed a write and shows that resuming creates no
duplicate.

There are two important limits. Hermes only performs discovery here; scripts
and tests make every tool call. The approval command records an identity string
but does not authenticate the person behind it. See [Limits](#limits) for the
full list.

Run the whole thing in one command:

```bash
./scripts/demo.sh
```

`./scripts/proof.sh` runs all 73 tests, inspects the MCP server, parses the
Compose file, and exercises the failure/resume path. The same test suite and a
fresh-clone check run in [GitHub Actions](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions).
No provider credentials are needed. [PROOF.md](PROOF.md) lists the check behind
each claim.

## What you can check

You can rerun each result below:

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

Hermes is an external client, not a Compose service. The discovery scripts use
an isolated `HERMES_HOME` and never touch `~/.hermes/config.yaml`. Hermes lists
the tools; `scripts/*.sh` and `pytest` call them.

## Limits

- `hermes mcp test` only discovers tools. It makes no provider call. A true
  model-driven run would require `hermes -z`, a tool-call transcript, and
  provider spend. I declined that spend on 2026-08-01, so this repository does
  not claim model-driven invocation.
- The demo calls the operator command with `demo-operator@example.com`. The MCP
  server cannot grant its own approval, but the lab does not authenticate that
  identity or prove human judgment. A real deployment would put this command
  behind authenticated operator access.
- `APPROVAL_STORE_PATH` is an unauthenticated JSON file. It stores only a SHA-256
  hash of the capability, but any local process that can edit the file can
  bypass the control. It is not a transactional authorization service.
- The `.jsonl` audit log has no signature, chain hash, or WORM storage.
  `run_started` and `run_finished` have a null correlation ID. The API has no
  audit log of its own, so direct API writes do not appear here.
- Deduplication is per approval, not per action. Two approvals for the same
  `action_id` have different idempotency keys and create two records.
- The server's allowlist is tested. Hermes's own `tools.include` behavior is
  not. On 2026-08-01, narrowing that list still made `hermes mcp test` print all
  three server-advertised tools.
- No second operator has run the validation steps. The blank
  [second-operator protocol](docs/second-operator-protocol.md) is a checklist,
  not a result, and nobody is scheduled to run it.
- The approval guard lives in the workflow runner. A client with the write token
  can bypass it and call the fixture API directly, with no audit entry.
- This is not a production deployment: no OIDC, Kubernetes, real identity
  provider, cloud/hybrid scaling, or customer data. It has one deterministic
  incident (`INC-2026-0042`), and a "write" adds a record to an in-memory store.
- CI parses `compose.yaml` but does not start containers; optional Podman/Docker smoke runs are not attested in the public tree.

## How it works

```
Hermes / script ──stdio──► enterprise-mcp ──Bearer+Idempotency-Key──► enterprise-api
        │                     ▲       │
        │ approval_id         │       └──────────────► audit log
        ▼                     │
 approval store ◄── operator command --approver <identity>
        │                     │
        └─ one-time capability (plaintext never persisted)
```

1. **Choose the surface.** The default allowlist exposes read/plan tools only.
2. **Read and plan.** `propose_incident_plan` returns runbook steps with stable
   `action_id`s and `approval_required` flags.
3. **Stop the first write.** `apply_incident_plan` without a capability writes
   nothing and returns only `pending_approval` plus an opaque `approval_id`.
4. **Get an operator grant.** `python -m workflow_runner.approval_operator approve
   <approval_id> --approver <identity>` records the approver and returns the
   expiring capability once; only its hash is persisted.
5. **Force the awkward failure.** With `ENTERPRISE_INJECT_FAILURE=error_after_commit`, the
   API commits the record and *then* returns 500. The caller sees `upstream_5xx`
   and resume instructions.
6. **Resume.** The same capability reuses the idempotency key; the API
   returns the original record with `replayed: true`.
7. **Check the result.** The store holds one record and another use of
   the applied capability is rejected without dispatch.

## Fresh-clone setup

Prerequisites: **Python 3.11, 3.12, or 3.13**. Not 3.14 — `pydantic-core` has no
wheel for it and its vendored PyO3 tops out at 3.13, so a source build fails.
Podman and the Hermes CLI are optional and only needed for the container and
Hermes proofs.

```bash
git clone https://github.com/dbett4/hermes-enterprise-deployment-lab
cd hermes-enterprise-deployment-lab
cp .env.example .env

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt \
                      -r workflow-runner/requirements.txt \
                      -r enterprise-mcp/requirements.txt
.venv/bin/python -m pytest -q

./scripts/demo.sh          # the whole arc; boots its own API, no containers needed
```

Optional container and Hermes checks:

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

Runs the FastMCP inspect/list/call checks without the Hermes CLI. Full local smoke
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

## More detail

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Components, checks, and security model |
| [`docs/runbook.md`](docs/runbook.md) | Operator commands and troubleshooting |
| [`docs/adr/003-stdio-mcp-read-plan-tools.md`](docs/adr/003-stdio-mcp-read-plan-tools.md) | Why stdio MCP |
| [`docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md`](docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md) | Historical two-call guard and credential/scoping decision |
| [`docs/adr/005-separated-operator-approval.md`](docs/adr/005-separated-operator-approval.md) | Superseding separated approval state machine and resume semantics |
| [`docs/second-operator-protocol.md`](docs/second-operator-protocol.md) | A validation checklist that has not been run |
| [`docs/build-spec.md`](docs/build-spec.md) | Original target and shipped status |

## Build status

| Milestone | Status |
|---|---|
| M1 Local deployment | Complete |
| M2 Identity/integration boundary | **Partial** — two static bearer scopes; no OIDC, no connector pagination/retry |
| M3 Agent workflow (MCP + Hermes discovery) | **Partial by design** — the real Hermes CLI discovers the scoped surface; no model-driven invocation (provider spend declined 2026-08-01) |
| M4 Separated approval, idempotency, resume, audit | **Partial** — role separation, expiry, terminal use, and ambiguous-failure resume work. Identity is supplied by the caller and the audit is not tamper-evident |
| M5 CI from a fresh clone | **Complete** — tests and fresh-clone job pass in GitHub Actions |

## Work not done

Nothing in this table is currently in progress.

| Item | State | Note |
|---|---|---|
| Production approval identity/policy integration | Not implemented | The operator command records a supplied identity but does not authenticate it. |
| Model-driven tool invocation | **Declined, 2026-08-01** | Provider spend declined. Permanent; this repository will never demonstrate it. |
| Second-operator validation | **Unrun** | `docs/second-operator-protocol.md` is a script nobody has executed. |
| CI run | Complete | Tests and the separate fresh-clone job pass in GitHub Actions. |
| Action-level deduplication | Not implemented | "Exactly once" is per approval, not per action. |
| Approval consumption and expiry | Implemented locally | `pending → approved → applied` or `expired`; applied/expired are terminal. |
| Authenticated approval store | Not implemented | Plain JSON at `APPROVAL_STORE_PATH`; capability plaintext is not persisted. |
| Enterprise-API-side audit | Not implemented | A direct write to the API leaves no trace. |

### What production approval would require

The local implementation returns an opaque request ID, grants through a
different command, records the supplied identity, stores a capability hash,
enforces expiry and binding, and safely resumes after an ambiguous commit. It
does not authenticate that identity or protect the JSON file from a local
writer. A production version would replace the command and file with an
IdP-backed approval service and transactional audit store. Details are in
[`docs/architecture.md`](docs/architecture.md).

## License

MIT — see [LICENSE](LICENSE). Security notes: [SECURITY.md](SECURITY.md).
