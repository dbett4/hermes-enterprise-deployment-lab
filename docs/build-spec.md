# Original build brief

> This is the plan I started with, kept to show how the implementation changed.
> It is not the current status. When it differs from the code or `README.md`,
> the shipped repository is authoritative. See
> [ADR 004](adr/004-enforced-approval-idempotency-and-scoped-tools.md).
>
> Three changes matter most:
>
> - The original implementation shipped a two-call self-approval guard. It was
>   superseded on 2026-08-11 by [ADR 005](adr/005-separated-operator-approval.md):
>   the MCP caller receives only an opaque request ID, while a distinct operator
>   command records identity and grants an expiring, terminal capability. The
>   lab still does not authenticate that identity through an enterprise IdP.
> - Wherever this brief implies an agent or model drives the workflow, that was
>   not implemented. Provider spend was declined on 2026-08-01. Every
>   call in the repository comes from a script or a test; Hermes's real role is
>   discovering and enumerating the MCP tool surface.
> - The repository was later published. Its test matrix and separate fresh-clone
>   job now run in GitHub Actions.

## Purpose

Build a public system that shows how I deploy, integrate, and debug Hermes in a
realistic local environment. It should be runnable code, not a screenshot or a
copy of private client infrastructure.

## What the lab was intended to cover

| Enterprise deployment responsibility | Lab proof |
|---|---|
| Cloud, on-prem, hybrid deployment | Local Compose profile plus a cloud-shaped deployment profile using the same contracts |
| Internal APIs/data systems/tooling | Mock enterprise operations API and a Hermes plugin/MCP integration |
| Authentication systems | Signed-service-token boundary with secrets kept out of source |
| Infrastructure/orchestration/model debugging | Failure fixtures, structured logs, trace IDs, health checks, and a troubleshooting runbook |
| Customer scoping and iteration | One customer scenario, explicit requirements, assumptions, acceptance tests, and change log |
| Reliability and observability | Readiness/liveness checks, retry budgets, timeouts, metrics/events, and alert examples |
| Reusable deployment patterns | Parameterized configuration, environment profiles, scripts, and documented extension points |
| Architecture and implementation documentation | Architecture diagram, ADRs, operator runbook, and demo walkthrough |

## Scenario

A fictional regulated-services company wants Hermes to coordinate an incident-intake workflow:

1. An internal service exposes incidents, accounts, and runbook metadata through an authenticated API.
2. Hermes receives a request from a messaging or CLI surface.
3. A custom integration retrieves only the permitted records.
4. The agent classifies the incident, gathers runbook context, and creates a local proposed action plan.
5. Consequential actions remain approval-gated; the lab never mutates an external production system.
6. Every run emits a correlation ID, step events, result status, latency, and an inspectable receipt.

## Milestones

### M1 — Green local deployment

- Compose starts all required services.
- Health checks become green without manual intervention.
- A smoke command calls the enterprise API through the Hermes integration.
- CI runs unit, contract, and Compose smoke tests.
- README gets a five-minute quickstart with expected real output.

### M2 — Integration and identity boundary

- API uses scoped authentication.
- Connector handles auth failure, pagination, timeout, malformed response, and upstream 5xx.
- No secrets appear in repository, logs, or receipts.
- Contract tests prove least-privilege record access.

### M3 — Agent workflow

- Hermes loads the MCP server via isolated `HERMES_HOME` and discovers three prefixed tools.
- FastMCP protocol smoke invokes all three tools deterministically.
- One end-to-end incident workflow produces a structured local receipt.
- Human approval is **represented** in receipts for consequential steps; no runtime approval gate or mutation execution. *(Superseded first by ADR 004, then by ADR 005: one mutating tool now requires a capability granted through the separate operator command.)*
- A failed dependency yields a useful recovery path rather than a generic error.

Original status on 2026-08-01: **Partial/Yellow** while Hermes discovery was
still outstanding. The shipped repository now includes the local Hermes
discovery checks; CI covers the protocol, test suite, and fresh-clone path, not a
live Hermes CLI run.

### M4 — Observability and failure injection

- Correlation IDs span gateway/request, integration call, agent operation, and receipt.
- Metrics include run count, success/failure, latency, retry count, and dependency status.
- Failure scripts cover API timeout, bad token, unavailable telemetry, and agent/provider error.
- Troubleshooting guide maps symptoms to evidence and recovery steps.

### M5 — Cloud/hybrid shape

- Add a second Compose overlay or low-cost cloud deployment path only after M1–M4 are green.
- Document network boundaries, secret injection, persistent state, backup/recovery, and upgrade strategy.
- Kubernetes is optional; add it only if the deployment is tested in CI and explainable end to end.

### M6 — Portfolio packaging

- Record a concise demo.
- Publish architecture diagram and two ADRs.
- Add an honest tradeoffs and limitations section.

## Quality gates

- No client names, data, screenshots, formulas, IDs, URLs, credentials, or proprietary LSL/Workiva source.
- No copied private repository history.
- No claim that the lab is a production customer deployment.
- Every advertised command is exercised in CI or a checked local smoke.
- Every documented failure mode has a committed fixture or test.
- Docker images and dependencies are pinned or bounded intentionally.
- A reviewer can understand the security boundary and recovery behavior without reading all source files.

## Original definition of done

A fresh clone can start the stack, pass health and contract checks, execute the incident workflow, display correlated operational evidence, survive at least three injected failures with documented recovery, pass CI, and support a five-minute technical walkthrough without private context.

## Explicit non-goals

- Generic chatbot UI
- Model training or fine-tuning
- Autonomous incident remediation
- Keyword-only Kubernetes manifests
- Real client/CRM/identity/data-warehouse integrations in the public version
