# Security Policy

## Scope

This repository is a **fixture lab** for demonstrating Hermes Agent integration
with a mock enterprise API. It is not a production deployment, does not contain
real customer data, and must not be used with production credentials.

## Reporting vulnerabilities

If you discover a security issue in this lab's code or documentation, open a
GitHub issue in the repository where you obtained this copy, or contact the
maintainer directly. Do not disclose sensitive details in public issues if they
could affect live systems outside this fixture lab.

## What not to submit

Never commit, paste, or include in issues or receipts:

- Real API keys, bearer tokens, or OAuth credentials
- Client names, production URLs, or proprietary business data
- Screenshots or exports from live Workiva, CRM, or identity systems
- Contents of your live `~/.hermes/config.yaml` or `~/.hermes/.env`

The lab uses fixture tokens (`lab-read-token`, `lab-write-token`) documented in
`.env.example`. Treat them as non-secret test data only.

## Credential handling — corrected 2026-08-01

An earlier version of this file claimed the token was "resolved from the operator
environment at connect time". That was **false for the FastMCP client path**.
MCP stdio forwards only an allowlisted set of variables
(`HOME`, `LOGNAME`, `PATH`, `SHELL`, `USER`), so `ENTERPRISE_API_TOKEN` never
reached the server process, and the server fell back to a hardcoded default. A
smoke run with a deliberately wrong token still succeeded.

What is true now:

- `enterprise_mcp.config.load_settings()` has **no default token**. A missing
  `ENTERPRISE_API_TOKEN` raises and the server exits 2 before serving.
- `scripts/run-enterprise-mcp.sh` also fails closed with an explicit message.
- Clients pass the full environment explicitly (`env=` on the stdio transport).
  Hermes does this through the `env:` block in its MCP server config, which
  supports `${VAR}` interpolation — verified on 2026-08-01 by inspecting the
  environment observed inside the spawned subprocess.
- The protocol smoke includes a **negative control**: a wrong token must produce
  an `auth_failure` receipt. The previous smoke was structurally unable to fail
  that assertion.

See [ADR 004](docs/adr/004-enforced-approval-idempotency-and-scoped-tools.md).

## Token scopes

| Variable | Scope | Grants |
|---|---|---|
| `ENTERPRISE_API_TOKEN` | read | `GET` incident, runbook, applied-action list |
| `ENTERPRISE_API_WRITE_TOKEN` | write | `POST` an incident action; superset of read |

A read-scope token presented to the mutating endpoint returns `403`. If no write
token is configured, `apply_incident_plan` cannot mutate and says so in its
response (`credential_scope: read_token_only`).

## Separated operator approval

`apply_incident_plan` is the only mutating tool, and it is guarded at runtime:

- Called without `approval_capability`, it returns only an opaque `approval_id`
  and sends **no write request** — asserted in tests against observed HTTP
  traffic, not against the response body's own claim.
- A separate `workflow_runner.approval_operator` command records the supplied
  approver identity and yields an expiring capability once. Only the capability
  hash is persisted.
- A forged capability, or one granted for a different incident or action, is
  refused with no write request.
- The approval carries an idempotency key, so a resume after a failure replays
  rather than re-applies.
- Confirmed apply/replay makes the approval terminal; further presentation is
  refused before dispatch.

**This is structural separation, not production identity assurance.** The MCP
caller cannot approve its own request through the tool surface, but the lab does
not authenticate the identity string supplied to the operator command. The demo
automates both roles with a fixture identity so it remains deterministic; it does
not prove a real person's identity or judgment.

Further limits worth stating plainly:

- The guard lives in the workflow layer. It is not a Hermes policy control and
  not an API-side authorization rule. A different client holding the write token
  could call the enterprise API directly and bypass it — and because the
  enterprise API keeps no audit of its own, that write would leave no trace.
- **The approval store is unauthenticated.** It is a plain JSON dict keyed by
  opaque request ID at `APPROVAL_STORE_PATH`, with no signature or ownership
  check. Hashing the capability prevents plaintext recovery from the file; it
  does not protect the state from a local writer. Trusted-filesystem-only.
- **Approval lifecycle is local.** `pending`, `approved`, `applied`, and
  `expired` are enforced, but by a JSON file and an in-process lock rather than a
  transactional authorization service.
- **Deduplication is per approval, not per action.** Two approvals for the same
  `action_id` carry two idempotency keys and produce two records.

## Tool-surface scoping

`ENTERPRISE_MCP_ENABLED_TOOLS` decides which tools are registered. Excluded tools
are absent from `list_tools` and not callable. The default surface is read/plan
only; the mutating tool is opt-in.

Hermes's own `tools.include` list is **not** proven to be enforced by this lab —
`hermes mcp test` reports the server's advertised surface, so it cannot
distinguish an enforced include list from an ignored one.

## Audit trail

`.audit/*.jsonl` is append-only and records tool invocation, approval
request/grant/rejection, mutation attempt, commit, replay, and failure, each with
a correlation ID and run ID. It is gitignored; scrub before sharing.

It is append-only **by convention**, not tamper-evident: a plain `.jsonl` file
with no signature, chain hash, or WORM property. `run_started` and `run_finished`
carry a null correlation ID. The enterprise API writes no audit of its own, so
the log records only what the workflow runner chose to report.

## Supported use

- Local Python or Podman Compose on a developer machine
- Isolated `HERMES_HOME` for MCP discovery proof
- CI protocol-level smoke without live Hermes credentials

## Out of scope

OIDC, Kubernetes hardening, production secret management, multi-process-safe
approval storage, authenticated approval identity, independent human judgment,
and any real external mutation.

No LLM has ever invoked these tools and none will: a model-driven run requires
provider spend that was declined on 2026-08-01. Every call in this repository is
made by a script or a test. Hermes's role here is discovery and enumeration only.
