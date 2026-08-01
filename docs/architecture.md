# Architecture

## Components

```mermaid
flowchart LR
  hermes[Hermes Agent\nexternal client] -->|stdio MCP| mcp[enterprise-mcp\nlocal process]
  operator[Operator / CI] --> demo[scripts/demo.sh]
  operator --> mcpSmoke[scripts/mcp-smoke.sh]
  operator --> hermesProof[scripts/hermes-mcp-proof.sh]
  operator --> filterProof[scripts/hermes-tool-filter-proof.sh]
  operator --> smoke[scripts/smoke.sh]
  demo --> mcp
  mcpSmoke --> mcp
  hermesProof --> hermes
  filterProof --> hermes
  smoke --> runner[workflow-runner]
  runner -->|Bearer + X-Correlation-ID| api[enterprise-api]
  mcp -->|Bearer + X-Correlation-ID + Idempotency-Key| api
  api --> fixtures[(Deterministic fixtures)]
  api --> store[(Action store\nthe only side effect)]
  mcp --> audit[(Append-only audit log)]
  mcp --> approvals[(Approval store)]
```

| Component | Role | Runtime |
|---|---|---|
| `enterprise-api` | Mock authenticated enterprise operations API, read + write scopes | Compose container (`8080`) or local uvicorn |
| `workflow-runner` | Integration seam: retrieval, planning, approval gate, idempotent execution | Compose on-demand container / imported library |
| `enterprise-mcp` | FastMCP stdio server; tool surface chosen by an allowlist | Local process (no port) |
| **Hermes Agent** | External operator that discovers MCP tools via isolated `HERMES_HOME` | **Not** a Compose service |

## Proof layers

| Layer | Command | What it proves | What it does not |
|---|---|---|---|
| Full arc | `./scripts/demo.sh` | Scoped discovery, two-phase mutation guard, forced failure, resume, exactly-once, audit | No model is involved; the caller is the script, and it self-approves |
| FastMCP protocol | `./scripts/mcp-smoke.sh` | Tool list/inspect/call **with real credential injection**, plus a wrong-token negative control | Not a Hermes-side proof |
| Hermes discovery | `./scripts/hermes-mcp-proof.sh` | The real `hermes` CLI connects over stdio and lists the tools | No invocation, no LLM |
| Hermes scoping | `./scripts/hermes-tool-filter-proof.sh` | Hermes discovers 4 tools vs 1 depending on the server allowlist | Not Hermes's own `tools.include` enforcement |
| Workflow seam | `./scripts/smoke.sh` | Containerized workflow-runner produces a guarded receipt | Read/plan path only |
| Fresh clone | `./scripts/fresh-clone-check.sh` | A clean clone of HEAD installs, passes `pytest`, and passes `scripts/demo.sh` | Not a CI run; it does **not** re-run the Hermes, smoke, or container proofs |

## MCP tool surface

| Tool | Mutating | Behavior |
|---|---|---|
| `check_enterprise_api` | no | Health/readiness probe; reports whether a write credential is present; never returns a token |
| `get_incident_context` | no | Incident + runbook retrieval with per-dependency correlation IDs |
| `propose_incident_plan` | no | Plan receipt; consequential steps carry `approval_required` and a stable `action_id` |
| `apply_incident_plan` | **yes** | Two-phase-guarded, idempotent execution of one runbook step |

The surface is chosen by `ENTERPRISE_MCP_ENABLED_TOOLS` and applied **before**
registration, so an excluded tool is absent from `list_tools` and not callable.
The default is read/plan only.

Hermes prefixes MCP tool names as `mcp__<server>__<tool>` for the model-facing
toolset, but `hermes mcp test` prints bare names. Receipts here record the bare
names as observed and flag the prefixed form as asserted, not observed.

## Threat boundary

- **In scope:** two local fixture token scopes via environment, deterministic
  incident data, structured logs, correlation IDs, an in-lab mutation target,
  failure injection for operator drills.
- **Out of scope:** real customer identity providers, production secrets,
  external mutation, Hermes model/provider credentials.
- **Secrets:** `.env` is gitignored. The Hermes MCP config references
  `${ENTERPRISE_API_TOKEN}` and forwards it explicitly through its `env:` block —
  MCP stdio does not inherit the parent environment. Receipts, tool output, and
  logs must not echo tokens; tests assert this.

## Identity model

Two static bearer scopes:

| Token | Grants |
|---|---|
| `ENTERPRISE_API_TOKEN` (read) | `GET` incident, runbook, applied-action list |
| `ENTERPRISE_API_WRITE_TOKEN` (write) | `POST` an incident action; superset of read |

Missing credentials return `401`, wrong scope returns `403`. The MCP server has
**no default token** and exits 2 without one.

## Two-phase mutation guard (not human approval)

Enforced at runtime, not merely represented in a receipt.

```
apply_incident_plan(incident, action)                -> pending_approval, token minted, NO write sent
apply_incident_plan(incident, action, bad_token)     -> approval_rejected, NO write sent
apply_incident_plan(incident, other_action, token)   -> approval_rejected, NO write sent
apply_incident_plan(incident, action, valid_token)   -> write dispatched
```

**What is enforced:** a single call can never mutate. A mutation always requires a
second call carrying a token that a prior refusal minted, bound to that exact
`(incident_id, action_id)`. Tests assert "no write was sent" against observed HTTP
traffic rather than the response's own claim.

**What is not enforced: any human involvement.** The token is returned to the same
caller that was just refused (`workflow_runner/executor.py`, the
`pending_approval` payload). There is no out-of-band channel, no approver
identity, no expiry, and no second-party check, so an autonomous caller can
self-approve in its next call. `scripts/demo.sh` and every test in this
repository do exactly that — they mint the token and replay it to themselves.
Calling this "human-in-the-loop" would be false.

Two further limits on the same mechanism:

- The approval store is an unauthenticated JSON file at `APPROVAL_STORE_PATH`.
  Anything that can write it can plant a token `validate()` will accept.
- `validate()` never reads `status`, so a token is neither consumed nor expired.

Boundary: this is a workflow-layer control. It is not a Hermes policy control and
not an API-side authorization rule.

### Roadmap — what a real approval control would require

**Status: deferred by the owner on 2026-08-01. Not implemented, not scheduled.**
Recorded here so the gap is a known design decision rather than an oversight.

Turning the guard above into a genuine human-in-the-loop control needs four
changes, none of which exist today:

1. **Mint the token to a store, not to the caller.** The refusal response returns
   only an opaque `approval_id`. The token value never travels back to the caller
   that was refused.
2. **Grant it from a separate actor, out of band.** A distinct process or command
   (for example `approve <approval_id> --actor <name>`) flips the request to
   grantable and records who granted it. The granting identity is persisted with
   the approval.
3. **Bound it in time.** A TTL on the grant, checked at validation, plus
   single-use consumption so an `applied` approval cannot authorize a new commit.
4. **Validate against what was granted,** not merely against what was requested:
   `validate()` refuses anything not explicitly granted, expired, already
   consumed, or bound to a different action.

Until all four exist, every description of this mechanism in this repository must
say "two-phase guard", never "human approval".

## Idempotency and resume

The idempotency key is minted **with the approval**, not with the attempt, so a
resume reuses it automatically.

| Situation | Result |
|---|---|
| First approved call | `applied`, `replayed: false`, one record created |
| Same key again | `replayed: true`, original record returned, no new record |
| Failure after commit, then resume | `error` with resume instructions, then `replayed: true`; store count stays 1 |

## Failure injection

| Trigger | Effect |
|---|---|
| `?inject=error` / `X-Inject-Failure: error` | HTTP 500 **before** any commit |
| `?inject=error_after_commit` | Commits the record, **then** HTTP 500 — the case a naive retry double-applies |
| `?inject=timeout` | Sleeps `INJECT_TIMEOUT_SECONDS` then continues |

The MCP server reads its fault switch from `ENTERPRISE_INJECT_FAILURE`, so faults
are configuration rather than a tool argument an agent could set.

## Correlation, audit, and observability

- **Run-level** `correlation_id` on every tool payload and receipt; updated when
  the API returns `X-Correlation-ID`.
- **Per-dependency** `correlation_id` on each `dependency_calls[]` entry.
- **Append-only audit log** (`.audit/*.jsonl`), one JSON object per line, with
  `run_id`, `seq`, `event`, `actor`, `correlation_id`. Events: `run_started`,
  `tool_invoked`, `approval_requested`, `approval_granted`, `approval_rejected`,
  `mutation_attempted`, `mutation_committed`, `mutation_replayed`,
  `mutation_failed`, `run_finished`.
- Structured JSON logs on the API include `service`, `path`, `status`,
  `correlation_id`, and `elapsed_ms`.

## Limitations

A customer-shaped lab, not a customer deployment. The approval and audit stores
are local files sized for a single operator.

**No model has ever chosen or invoked any of these tools, and none will.** Every
call in this repository comes from a script or a test. A model-driven run would
require provider spend, which the owner declined on 2026-08-01. This is a
permanent ceiling on what the repository can demonstrate, not an open task. What
a real Hermes build did do is discover and enumerate the tool surface over stdio
(`scripts/hermes-mcp-proof.sh`, `scripts/hermes-tool-filter-proof.sh`).

The audit log is append-only by convention — a plain `.jsonl` file with no
signature or chain hash. It is not tamper-evident, `run_started` and
`run_finished` carry a null correlation ID, and the enterprise API writes no
audit of its own, so a direct write to the API leaves no trace in it.
