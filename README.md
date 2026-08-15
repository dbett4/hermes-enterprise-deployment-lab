# Hermes Enterprise Deployment Lab

> **Provenance.** Sanitized public extract published August 2026. Public git history
> is publication history, not the original private development timeline. Incident
> `INC-2026-0042`, operators, and API data are fictional fixtures. No client data or
> credentials appear in this repository. Private client history remains confidential;
> public claims are limited to inspectable artifacts.

## The problem this answers

When an AI agent can touch an internal system, the hard failures are not “the model
said something wrong.” They are operational:

1. **Too many tools** — the agent sees write paths it should not have.
2. **Self-approval** — the same path that proposes a change also grants it.
3. **Lies after success** — the API already committed, then returns an error; a naive
   retry double-writes.
4. **No receipt** — nobody can reconstruct who approved what, or whether resume was safe.

This lab is a **synthetic, runnable answer** to those failures. It is engineering
evidence, not a customer Hermes Enterprise deployment, not production identity
integration, and not a model-driven production run.

**One-line pitch:** scope the tools, separate the human operator from the agent,
survive the ugly post-commit failure, and leave exactly one side effect.

## What it is (and is not)

| It is | It is not |
|---|---|
| A local deployment lab with a mock enterprise API | A customer or cloud production deploy |
| Scoped MCP tools + a separate operator approval path | The agent approving its own writes |
| Forced failure after commit + safe resume | Proof that every real outage is handled |
| Credential-free tests and public CI receipts | Model-driven tool calls (scripts/tests call tools) |
| Optional container / telemetry / trace proofs | Kubernetes, OIDC, or operated multi-tenant infra |

## The story in one pass

```text
Agent (or script) sees only the allowed tools
        ↓
It can read and plan
        ↓
First write stops: pending approval, no mutation yet
        ↓
A separate operator command grants a one-time capability
        ↓
API commits — then we inject a 500 (the awkward case)
        ↓
Resume reuses the same idempotency key
        ↓
Exactly one record exists; the capability cannot be reused
```

Run the arc:

```bash
./scripts/demo.sh
```

## Try the proof path

No provider API keys. Default proof stays on the host (tests + failure/resume +
native telemetry/trace). Containers are optional / CI-attested.

```bash
./scripts/proof.sh
```

| You want… | Run… |
|---|---|
| Full local story | `./scripts/demo.sh` |
| Credential-free checks | `./scripts/proof.sh` |
| Container restart + replay (Docker/Podman) | `PROOF_WITH_CONTAINERS=1 ./scripts/proof.sh` or `bash ./scripts/container-proof.sh` |
| Claim table | [PROOF.md](PROOF.md) |
| Public CI authority | [Actions](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions) for the exact commit |

Treat a green Docker-backed `container-proof` job plus its uploaded receipt as the
authority for the container path. Cloud IaC jobs here are **validate only** (no
refresh/apply) and are never deployment evidence.

## Design choices that matter

1. **Tool surface is decided by the server**, not hoped for in the prompt.
2. **Approval is a different command path** from the MCP server ([ADR 005](docs/adr/005-separated-operator-approval.md)).
3. **Plaintext capability is returned once**; only a hash is stored.
4. **Post-commit failure is a first-class demo**, not an afterthought.
5. **Resume is idempotent** — replay, do not re-apply.
6. **Hermes is an external client** for discovery proofs; scripts and tests perform
   the tool calls. I declined model-spend for invocation on 2026-08-01, so this repo
   does not claim model-driven runs.

## Sister project

If this lab answers **“when an agent can touch a system, can we approve and recover
safely?”**, the related kit answers a different question:

**[Hermes Enterprise Evaluation Kit](https://github.com/dbett4/hermes-enterprise-field-kit)** —
can we govern Hermes for enterprise-shaped work with policy packs, independent
checks, and human review gates?

Same family. Different question.

## What you can check

You can rerun each result below. Prefer the plain-language claim; the command is the
receipt.

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
| A clean clone of HEAD reproduces the test suite and the demo | `./scripts/fresh-clone-check.sh` — it runs `pytest` and `scripts/demo.sh` only; it does **not** re-run native telemetry, trace, Hermes, or container proofs |
| The API exports bounded-cardinality request and mutation-outcome metrics, and Prometheus can scrape/query them | `./scripts/telemetry-proof.sh` — native localhost API + repository-pinned plus upstream-manifest-checked Prometheus; receipt under `.telemetry-proof/` |
| Five availability, latency, and mutation-safety alerts load and behave under positive and idle-series fixtures | `promtool test rules observability/alerts.test.yml`, executed by both telemetry proof paths |
| Workflow-runner CLIENT spans directly parent API SERVER spans (`SERVER.parent_span_id == CLIENT.span_id`) under the same W3C trace ID, with bounded approval/failure/resume events and no secret attributes | `./scripts/trace-proof.sh` — loopback OTLP/HTTP capture with a wrong-parent negative control; receipt under `.trace-proof/` |
| Compose API keeps one side effect across container restart and replay | Public CI run [`31891411678`](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions/runs/31891411678) at `3da5938`, job `container-proof` (artifact uploaded); no customer deployment or production traffic is implied |
| A LangGraph workflow retrieves tenant-scoped, cited runbook context, fails closed without tenant scope or supporting evidence, routes through explicit analysis and safety-review stages, and evaluates citation/safety integrity without executing actions | `.venv/bin/python -m pytest tests/test_agent_workflow.py -q`, `.venv/bin/python scripts/agent-workflow-proof.py`, and public CI run [`31891411678`](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions/runs/31891411678) at `3da5938` |

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
  bypass the control. Same-host writers are serialized with a separate
  `<path>.lock` via Linux `fcntl.flock`; this is a single-host demonstration,
  not a distributed or production authorization service.
- The `.jsonl` audit log has no signature, chain hash, or WORM storage.
  `run_started` and `run_finished` have a null correlation ID. The API has no
  audit log of its own, so direct API writes do not appear here.
- The workflow derives one idempotency key per incident/action pair and rejects
  a later approved capability after that pair is applied. Concurrent approvals
  still converge on the same downstream key. The enterprise action store also
  enforces one record per pair for direct write-token callers: a different key
  receives HTTP 409 rather than a silent replay or second record.
  Dispatch re-derives the current pair key instead of trusting the persisted
  approval field, so pending approvals from the older random-key format also
  converge after an upgrade.
- Existing file-backed stores containing duplicate pairs fail closed on load.
  Preserve and reconcile or quarantine that fixture JSON before restart; the
  API cannot start merely to call its reset endpoint while the file is invalid.
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
  incident (`INC-2026-0042`). A write adds a record to an in-memory store by
  default, or to an optional JSON file when `ACTION_STORE_PATH` is set (Compose
  uses a volume so restart proofs can survive process death).
- The workflow defines a `container-proof` job intended to check negative auth,
  post-commit failure, restart persistence, replay, and demo against the
  containerized API. A runtime pass applies only to an exact commit whose
  Docker-capable job is green and whose uploaded receipt reports a pass. Even
  then, it does not prove Kubernetes, OIDC, cloud deploy, or model-driven
  invocation. Missing Docker/Podman engine access fails closed (exit 2).
  `proof.sh` does not start containers unless you opt in; its default telemetry
  check still starts and cleans up temporary native processes.
- Prometheus metrics, SLO expressions, and alert-rule fixtures are implemented
  and proven with native localhost processes. Opt-in OpenTelemetry tracing is
  proven with loopback OTLP/HTTP capture. There is no Alertmanager, pager,
  collector backend, retention system, or production traffic. Native proofs are
  not evidence that the Compose telemetry path ran.
- The hardened Stage-1 LangGraph proof is public CI-attested synthetic evidence at
  `3da5938` / run [`31891411678`](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions/runs/31891411678). Retrieval is exact keyword-token overlap over an
  in-script two-document fixture: any shared token, including a common word,
  produces a nonzero score and `ready_for_review`. This is not semantic relevance.
  There is no vector store, model call, action execution, authorization, or
  external validation. The evaluator is a regression check, not a mutation gate.

## How it works

```
Hermes / script ──stdio──► enterprise-mcp ──Bearer+Idempotency-Key──► enterprise-api
        │                     ▲       │
        │ approval_id         │       └──────────────► audit log
        ▼                     │
 approval store ◄── operator command --approver <identity>
        │                     │
        └─ one-time capability (plaintext never persisted)

Prometheus ──scrape /metrics──► enterprise-api
OTLP capture (opt-in, loopback) ◄── workflow-runner CLIENT + enterprise-api SERVER
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
The native telemetry proof also needs `curl`, `sha256sum`, `tar`, and network
access to the pinned Prometheus release. The trace proof needs `curl` only and
stays on loopback. Docker or Podman and the Hermes CLI are
optional and only needed for the container and Hermes proofs.

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

Optional focused and self-contained container checks:

```bash
./scripts/telemetry-proof.sh          # native API + Prometheus, no containers
./scripts/trace-proof.sh              # native API + loopback OTLP capture, no containers
bash ./scripts/container-proof.sh     # dynamic free host ports; restart + replay + demo; performs teardown
```

For a manual fixed-port Compose stack that stays up for Hermes/MCP checks, start
and stop it separately from `container-proof.sh`:

```bash
docker compose up -d --build enterprise-api prometheus
ENTERPRISE_API_URL=http://127.0.0.1:8080 ./scripts/mcp-smoke.sh
ENTERPRISE_API_URL=http://127.0.0.1:8080 ./scripts/hermes-tool-filter-proof.sh
docker compose down -v --remove-orphans
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
| `ACTION_STORE_PATH` | Optional JSON path for applied actions. Unset = in-memory. Compose sets a volume path |
| `ENTERPRISE_API_PORT` | Optional loopback host port for the Compose API; defaults to 8080 outside the container proof |
| `PROMETHEUS_PORT` | Optional host port for the Compose Prometheus service; defaults to 9090 outside the container proof |
| `OTEL_TRACES_EXPORTER` | Opt-in tracing. Must be `otlp` together with a loopback OTLP endpoint or tracing stays off |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Loopback OTLP/HTTP collector. Non-loopback endpoints are ignored |

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
| [`docs/slo.md`](docs/slo.md) | 99%/95% objectives, burn-alert math, trace allowlist, and evidence limits |
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
| M5 CI from a fresh clone | **CI-attested at `3da5938`** — test, fresh-clone, and Docker-backed container restart/replay jobs are green with uploaded receipts in run [`31891411678`](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions/runs/31891411678) |
| Native telemetry extension | **CI-attested at `3da5938`** — live native scrape/query plus positive and negative rule tests pass; no external notification delivery or production traffic is claimed |
| Native trace extension | **CI-attested at `3da5938`** — loopback OTLP/HTTP capture of W3C CLIENT/SERVER spans and bounded approval events passes; no collector backend, retention, or production traffic is claimed |
| Cloud/hybrid IaC reference | **CI-validated at `3da5938` / not deployed** — pinned OpenTofu and AWS-provider plans exercise a disabled zero-resource graph and an enabled private Fargate graph without AWS refresh or apply |

## Work not done

These boundaries remain open or deliberately out of scope.

| Item | State | Note |
|---|---|---|
| Production approval identity/policy integration | Not implemented | The operator command records a supplied identity but does not authenticate it. |
| Model-driven tool invocation | **Declined, 2026-08-01** | Provider spend declined. Permanent; this repository will never demonstrate it. |
| Second-operator validation | **Unrun** | `docs/second-operator-protocol.md` is a script nobody has executed. |
| CI container-proof run | **Per-commit evidence gate** | Treat the container path as attested only when the exact commit's Docker-capable Actions job is green and its uploaded receipt reports a pass. |
| CI cloud-IaC proof run | **Per-commit validation gate** | The read-only job validates no-refresh/no-apply plans; its status is visible in Actions and cannot prove deployment or runtime behavior. |
| Action-level deduplication | CI-attested at `3da5938` | Workflow approvals share a deterministic pair key, and the locked enterprise action store rejects a different-key duplicate pair with HTTP 409. Single-host fixture invariant only. |
| Approval consumption and expiry | CI-attested at `3da5938` | `pending → approved → applied` or `expired`; applied/expired are terminal. |
| Authenticated approval store | Not implemented | Plain JSON at `APPROVAL_STORE_PATH`; capability plaintext is not persisted. |
| Enterprise-API-side audit | Partial request/conflict logging only | Direct requests have structured request logs and dedup conflicts add a bounded event with a key hash; there is no append-only action audit. |
| External alert delivery | Not implemented | Prometheus loads and evaluates rules; no Alertmanager or pager is configured. |
| OpenTelemetry traces | CI-attested at `3da5938` | Opt-in loopback OTLP/HTTP proof only. No collector backend, retention, or production traffic. |

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
