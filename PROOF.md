# How to check the claims

After installing the development and component requirements, run:

```bash
./scripts/proof.sh
```

It stops at the first failure and prints `LAB_PROOF_PASS` only after every check
below passes. The default path starts temporary localhost API, Prometheus, and
OTLP-capture processes, but it does not start containers. The workflow defines
a separate `container-proof` CI job. A container-runtime pass applies only to
an exact commit with a green Docker-backed job and a passing uploaded receipt.
On a Docker-capable host, opt in with `PROOF_WITH_CONTAINERS=1 ./scripts/proof.sh` or
`./scripts/proof.sh --with-containers`, or call
`bash ./scripts/container-proof.sh` on its own.

| Claim | Run | Pass condition | Limit |
|---|---|---|---|
| The MCP server is real and its tool list is scoped | FastMCP inspect plus the test suite | Four tools exist in write-enabled mode; the mutator is absent and uncallable in the default read/plan scope | CI checks the MCP protocol; this public extract includes no Hermes runtime receipt and does not claim model invocation |
| The mutating caller cannot mint its own approval capability | Approval and MCP tests | The first call returns only an opaque `approval_id`; the separate operator command records an identity and returns the plaintext capability once | The identity is a caller-supplied fixture string, not authenticated by an IdP |
| Capabilities are bound, expiring, and terminal | Approval-store and executor tests | Forged, cross-incident, cross-action, expired, and already-applied capabilities reject before POST | The local JSON store is not a transactional production authorization service |
| An ambiguous retry, distinct approval, or direct re-key cannot duplicate one action | `./scripts/demo.sh`, executor, API contract, approval-store, and action-store concurrency tests | Dispatch re-derives one action-scoped key even for a legacy approval; concurrent capabilities converge; the locked action store rejects different-key duplicate pairs with HTTP 409 and fails closed on persisted duplicates | Single-host fixture invariant only; not distributed exactly-once, authenticated approval identity, or production durability |
| Credential scope fails closed | API, stdio-injection, and demo tests | Read scope receives 403; missing server credentials abort startup; a wrong explicitly injected token fails | Static fixture bearer tokens stand in for enterprise identity |
| Native metrics and alerts are executable | `./scripts/telemetry-proof.sh` | A repository-pinned plus upstream-manifest-checked Prometheus 3.13.2 binary scrapes the API, queries route-template and action-outcome metrics, loads five alerts, and passes positive firing plus idle-series negative fixtures | Native localhost processes and synthetic traffic only; no Compose, Alertmanager delivery, or production telemetry |
| Native traces propagate across the approval mutation | `./scripts/trace-proof.sh` | Loopback OTLP/HTTP capture requires an API SERVER span whose trace ID matches the workflow CLIENT span and whose `parent_span_id` equals that CLIENT `span_id`, plus bounded pending/failure/resume events and no tokens, capabilities, keys, notes, or bodies | Sampled local capture only; no collector backend, retention, or production traffic |
| Cloud/hybrid IaC is statically executable and cost-bounded by validation | `./scripts/cloud-iac-proof.sh` | Repository-pinned OpenTofu plus lock-file-pinned AWS provider format/validate; disabled plan has zero managed changes; enabled `-refresh=false` plan has one private Fargate task, internal TLS ALB, ECR/Secrets-scoped execution IAM, EFS TLS/IAM access-point authorization with scoped task mount/write policy, known-empty ALB/EFS egress, alarms, rollback, and an alert budget ≤USD 25 | No AWS refresh/apply, account, deployment, image startup, runtime, notification delivery, exact bill, or hard spend enforcement; GitHub/provider registry downloads occur |
| Compose-backed API keeps one side effect across restart and replay | `bash ./scripts/container-proof.sh`; public CI runs `31637042354` and `31845098855` | A Docker-capable pass must show negative auth leaves count=0; post-commit 500 leaves count=1; after `enterprise-api` restart count is still 1; same key replays; demo against that API still ends with one side effect. CI evidence is bound to the exact commit and uploaded receipt | Needs a usable `docker compose` or `podman compose` engine (CLI alone fails closed). Proves fixture JSON on a volume, not production storage, K8s, or OIDC |
| A new checkout reproduces the result | Current pytest suite, demo, and the separate fresh-clone CI job | Local checks complete without provider credentials | The fresh-clone path does not run native telemetry, trace, Hermes, smoke, or container checks; `proof.sh` derives its test count from pytest output and runs the separate native telemetry and trace proofs |
| Stage-1 LangGraph cited retrieval, safety review, and evaluation | `.venv/bin/python -m pytest tests/test_agent_workflow.py -q` and `.venv/bin/python scripts/agent-workflow-proof.py` | Local candidate receipt has `evaluation_passed: true`, empty `executed_actions`, and hashed document/action provenance checked against the proof boundary's separately supplied authoritative fixture, plus the pinned `input_sha256` / `result_sha256` | Local-only until a green public CI run on the adopting commit; run `31845098855` predates the provenance hardening and does not attest it. The evaluator rejects missing external authority, self-signed provenance, citation laundering, and changed authoritative action fields, but remains a regression check rather than authz. The local fixture is not signed. No vector store, model call, action executor, authz, or external validation. |

## Stage-1 LangGraph proof details

The baseline Stage-1 synthetic proof is public CI-attested at `6a8c437` / run
`31845098855`. The action-provenance hardening in this working tree is local
only until a green public CI run on its adopting commit. Neither version is
part of the deployment/recovery/telemetry/container evidence.

Commands:

```bash
.venv/bin/python -m pytest tests/test_agent_workflow.py -q
.venv/bin/python scripts/agent-workflow-proof.py
```

The CI `test` job tees the proof script under `.agent-workflow-proof/` with
`set -euo pipefail` so a Python exit 1 is not masked by `tee`. The
`fresh-clone` job runs the same script after pytest. Local `proof.sh` still
does not invoke it.

Limits: exact keyword-token overlap over an in-script two-document fixture. Any
single shared token, including a common word, produces a nonzero score and a
`ready_for_review` result; this is not a semantic relevance guarantee. No vector
store, model call, action executor, authorization, or third-party/external
validation. `agent_workflow/evaluation.py` is a regression check on graph JSON,
not a mutation gate.

## CI

The workflow runs `test` and `fresh-clone` jobs, extends `test` with the native
telemetry and loopback OTLP trace proofs, and defines separate
`container-proof` and `cloud-iac-proof` jobs. The latter runs the
repository-pinned, no-refresh, no-apply OpenTofu proof and uploads its sanitized
receipt and command log. Per-commit Actions state and uploaded receipts are the
CI authority. A container claim additionally requires a green Docker-capable
job; the cloud job never proves an apply, deployment, runtime, or actual spend.
`scripts/fresh-clone-check.sh` can also check the committed revision locally; it
deliberately clones `HEAD`, so run it after committing changes.

## Native telemetry proof details

`scripts/telemetry-proof.sh` downloads the pinned Prometheus 3.13.2 release and
requires `actual == repository_pin == upstream sha256sums.txt` for the selected
Linux amd64/arm64 archive before extraction. It runs
`promtool` syntax, rule, metric, and rule-unit checks; starts the API and
Prometheus on temporary loopback ports; sends synthetic created/replayed/
post-commit-error traffic; then queries target health, loaded alerts, and metric
series. The receipt under `.telemetry-proof/` records the release hash, target,
alerts, rule-test coverage, and observed outcomes without tokens.

The proof cleans up both processes. It proves no container image, Compose
network, external alert delivery, or production traffic. Distributed traces are
a separate native proof.

## Native trace proof details

`scripts/trace-proof.sh` starts a loopback-only OTLP/HTTP capture endpoint and
the API on free ports, then runs the separated approval, post-commit failure,
and resume/replay path through workflow-runner. Both providers flush. The script
parses captured OTLP protobuf and requires direct CLIENT→SERVER causality: the
API SERVER span must share the workflow CLIENT trace ID and have
`parent_span_id == client.span_id`. It also requires the bounded failure/resume
events and fails if
fixture tokens, capabilities, idempotency keys, notes, request bodies, or
incident/action identifiers appear in captured bytes or the receipt.

The receipt under `.trace-proof/` records span names, shared trace IDs, verified
CLIENT→SERVER parent links, and event sequences without secrets. A tampered
same-trace/wrong-parent negative control must fail. `SimpleSpanProcessor` is
lab-only. This is not a collector backend, retention system, or production
trace pipeline.

See [docs/slo.md](docs/slo.md) for the 99% / 95% objectives, burn-alert
arithmetic, and forbidden trace attributes.

## Container proof details

`scripts/container-proof.sh` prefers `docker compose`, else `podman compose`,
and requires a working engine (`docker info` / `podman info`), not just a CLI.
It builds and starts `enterprise-api` and pinned Prometheus, with
`ACTION_STORE_PATH` on a named volume, waits on Compose healthchecks plus
`/healthz` and `/readyz`, proves the telemetry target/rules and mutation metrics,
then proves
missing/read/bad tokens create no side effect, forces `error_after_commit`,
restarts the API container, checks the record survived, replays the same
idempotency key, then runs `scripts/demo.sh` with `ENTERPRISE_API_URL` pointed
at the proof-selected loopback port. It assigns an isolated Compose project and
free API/Prometheus ports so teardown cannot remove another lab stack. Receipts
and redacted logs land under `.container-proof/` (gitignored). A trap always runs
best-effort `compose down -v` for that isolated project on failure. The success
path requires teardown to complete before it writes a pass receipt or marker.

Host vs container in that script:

- Container: `enterprise-api`, Prometheus, and their named volumes
- Host: curl restart/telemetry probes, MCP stdio, and the operator command in `demo.sh`
