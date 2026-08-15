# Hermes Enterprise Deployment Lab

`./scripts/demo.sh` boots a local enterprise API and walks one runbook action all
the way through the failure that actually hurts: the API commits the write, then
returns 500. Ten steps, no containers, no API keys, no model. What follows is what
the code does, step by step, with the file and test that establishes each claim.

## The arc the demo runs

Everything below is `enterprise-mcp/enterprise_mcp/demo.py`, which `scripts/demo.sh`
executes and `enterprise-mcp/tests/test_demo_arc.py` runs as a test, so the demo
cannot rot into a story the code no longer tells. Every tool call crosses a real MCP
stdio boundary into a freshly spawned `enterprise_mcp.server` process. The caller is
the script; no model has ever driven this arc.

**1–2. The tool surface is decided by the server.** With the read/plan allowlist,
`list_tools` returns three tools and `apply_incident_plan` is not among them —
`build_server` in `enterprise-mcp/enterprise_mcp/server.py` never registers a tool
outside the allowlist, so an excluded tool is not merely refused, it does not exist on
the wire. Calling it raises. Switch the allowlist to `all` and four tools appear.
Proof: `enterprise-mcp/tests/test_tool_filtering.py`, and differentially against the
real Hermes CLI in `scripts/hermes-tool-filter-proof.sh` (4 tools vs 1).

**3–4. The first write stops itself.** `propose_incident_plan` returns runbook steps
with stable `action_id`s and `approval_required` flags. Calling `apply_incident_plan`
without a capability returns `status: pending_approval` and an opaque `approval_id` —
no capability, no idempotency key, and no HTTP write is issued at all. The demo counts
the action store before and after to show the count did not move. Proof:
`workflow-runner/tests/test_executor.py::test_missing_approval_issues_only_an_id_and_writes_nothing`,
asserted against observed HTTP traffic, and
`enterprise-mcp/tests/test_approval_and_resume_over_mcp.py::test_unapproved_call_makes_no_side_effect`
over the real stdio boundary.

**5. Approval comes from a different command.** `python -m
workflow_runner.approval_operator approve <approval_id> --approver <identity>` is a
separate process the MCP server cannot invoke on its own. It records the approver,
returns an expiring capability exactly once, and persists only its SHA-256 hash.
Proof: `workflow-runner/tests/test_executor.py::test_operator_identity_is_recorded_and_plaintext_capability_is_not`;
rationale in [ADR 005](docs/adr/005-separated-operator-approval.md). Forged, expired,
wrongly bound, and already-applied capabilities are each refused before dispatch
(`test_forged_capability_writes_nothing`, `test_expired_capability_is_terminal_and_writes_nothing`,
`test_capability_bound_to_a_different_incident_is_refused`, `test_pending_request_cannot_be_used_as_a_capability`).

**6. The ambiguous failure, on purpose.** With
`ENTERPRISE_INJECT_FAILURE=error_after_commit`, `enterprise-api/app/main.py` writes the
record and *then* returns 500. The caller sees `upstream_5xx` plus resume instructions
and cannot tell from the response whether the write landed. It did.

**7–8. Resume replays instead of re-applying.** The executor re-derives the
incident/action pair's idempotency key at dispatch — it does not trust the key stored
on the approval — so the resumed call returns the original record with
`replayed: true`. The store holds exactly one record, and a third use of the same
capability is refused with `approval_already_applied` without dispatching anything.
Proof: `enterprise-mcp/tests/test_approval_and_resume_over_mcp.py::test_forced_failure_then_resume_leaves_one_side_effect`,
plus `workflow-runner/tests/test_executor.py::test_two_distinct_approvals_for_same_action_create_only_one_record`
and `test_concurrent_distinct_approvals_converge_on_one_side_effect`. The API's action
store enforces the same one-record-per-pair invariant independently, returning HTTP 409
to a direct write-token caller who supplies a different key
(`enterprise-api/tests/test_actions.py`).

**9–10. Scope and trail.** A write attempted with only the read credential is refused by
the API with `auth_failure` and produces no record. The run's `.jsonl` audit log holds
request, named operator grant, capability acceptance, failure, and replay events
(`workflow-runner/tests/test_audit.py`). The log is not tamper-evident; see
[Limits](#limits).

The whole arc runs from a clean clone in `./scripts/fresh-clone-check.sh`, and against
the containerized API in the Docker-backed CI `container-proof` job — public CI run
[`31891411678`](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions/runs/31891411678)
at `3da5938`.

> **Provenance.** This repository has real pre-publication history: the first 11
> commits (`git log --reverse --date=iso`, 2026-07-27 through 2026-08-01) predate
> publication; nothing was squashed into a publication commit. Incident `INC-2026-0042`,
> its runbook, the operator identities, and the API data are **fictional fixtures**
> (`enterprise-api/app/fixtures.py`); no client data or credentials appear here, and
> private client history stays private.

## What is in here

| Capability | Where | State |
|---|---|---|
| Scoped MCP tool surface over stdio | `enterprise-mcp/enterprise_mcp/server.py` | Server-side allowlist; verified differentially against the real Hermes CLI |
| Separated operator approval, expiry, terminal capabilities | `workflow-runner/workflow_runner/approvals.py`, `approval_operator.py` | Working; identity is supplied, not authenticated |
| Idempotent execution and post-commit resume | `workflow-runner/workflow_runner/executor.py`, `enterprise-api/app/store.py` | Working; one record per incident/action pair, enforced on both sides |
| Mock enterprise API with deterministic fault injection | `enterprise-api/app/` | Working; in-memory by default, optional JSON file for restart proofs |
| **LangGraph agent workflow** — retrieval → analysis → safety review, fail-closed, with a citation/provenance/safety evaluator | `agent_workflow/graph.py`, `retrieval.py`, `evaluation.py` | Stage-1, read-only: it plans and evaluates, it never executes an action. `pytest tests/test_agent_workflow.py`, `scripts/agent-workflow-proof.py` |
| Audit trail | `workflow-runner/workflow_runner/audit.py` | Append-only `.jsonl`; no signature or chain hash |
| Metrics, alert rules, SLO math | `enterprise-api/app/metrics.py`, `observability/`, `docs/slo.md` | Native localhost proof; no Alertmanager or pager |
| OpenTelemetry tracing | `enterprise-api/app/tracing.py`, `workflow-runner/workflow_runner/tracing.py` | Opt-in, loopback OTLP capture only |
| Cloud/hybrid IaC reference | `docs/cloud-hybrid-reference.md`, `scripts/cloud-iac-proof.sh` | Validate-only OpenTofu plans; never deployed |

The LangGraph pipeline is deliberately the read-only half: it retrieves tenant-scoped
runbook context with citations, fails closed without tenant scope or supporting
evidence, and routes through explicit analysis and safety-review stages. Its retrieval
is exact keyword-token overlap over an in-script fixture — not semantic relevance — and
its evaluator is a regression check, not a mutation gate. The mutation gate is the
approval machinery above.

## Two repositories, one family

This lab is the execution half. The governance half — policy packs, approved
configurations, independent checks, and human review gates — is the
[Hermes Enterprise Evaluation Kit](https://github.com/dbett4/hermes-enterprise-evaluation-kit),
whose S3 **Act** mission runs against this lab's MCP tools. The split, and the exact
code path that connects them, is in
[`docs/hermes-enterprise-family.md`](docs/hermes-enterprise-family.md).

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

Hermes itself is an external client, not a Compose service: `hermes mcp test` lists the
tools under an isolated `HERMES_HOME` and never touches `~/.hermes/config.yaml`. It
discovers; `scripts/*.sh` and `pytest` call. Model-driven invocation would need
provider spend, which I declined on 2026-08-01 — this repository will not claim it.

## What you can check

Each row is a rerunnable command. The command is the receipt.

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
- An [independent AI-agent clean-checkout validation](docs/independent-ai-validation-2026-08-15.md)
  passed every executable native, demo, Hermes discovery, differential-filter,
  and adversarial path at `3da5938`. It ran on the same VPS, not a different
  physical machine, and its container step was skipped because the validator
  could not access a Docker/Podman daemon. It is not human second-operator
  validation; the public Docker-capable CI result remains separate evidence.
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

## Component map

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

The step-by-step behaviour behind this diagram is the walkthrough at the top of this
file; the security model is in [`docs/architecture.md`](docs/architecture.md).

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
| [`docs/second-operator-protocol.md`](docs/second-operator-protocol.md) | The validation checklist and current human/AI-agent gate status |
| [`docs/independent-ai-validation-2026-08-15.md`](docs/independent-ai-validation-2026-08-15.md) | Independent AI-agent clean-checkout receipt at `3da5938`; same VPS, not human, container skipped |
| [`docs/build-spec.md`](docs/build-spec.md) | Original target and shipped status |
| [`docs/hermes-enterprise-family.md`](docs/hermes-enterprise-family.md) | How this lab and the Evaluation Kit divide the problem, and the code path that joins them |

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
| Second-operator validation | **AI-agent clean-checkout PASS_WITH_LIMITATIONS; human/different-machine unrun** | The [published receipt](docs/independent-ai-validation-2026-08-15.md) records 240 tests plus demo, native telemetry/trace, Hermes discovery/filter, and adversarial passes. Validator-local container execution was skipped; exact-commit public CI supplies separate Docker evidence. |
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
