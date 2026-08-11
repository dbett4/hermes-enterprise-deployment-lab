# Architecture

## Components

```mermaid
flowchart LR
  hermes[Hermes Agent\nexternal client] -->|stdio MCP| mcp[enterprise-mcp\nlocal process]
  operator[Operator / CI] --> demo[scripts/demo.sh]
  operator -->|approve ID + identity| approvalCommand[approval_operator command]
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
  approvalCommand --> approvals
  approvals -->|expiring capability| mcp
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
| Full arc | `./scripts/demo.sh` | Scoped discovery, caller/operator separation, forced failure, resume, terminal capability, exactly-once, audit | No model is involved; the script automates the operator command with a fixture identity |
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
| `apply_incident_plan` | **yes** | Requests approval or consumes a separately granted capability to execute one runbook step idempotently |

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

## Separated operator approval

Enforced at runtime, not merely represented in a receipt.

```
apply_incident_plan(incident, action)                  -> pending_approval + opaque ID, NO write
approval_operator approve <ID> --approver <identity>  -> expiring capability, identity recorded
apply_incident_plan(incident, action, bad_capability) -> approval_rejected, NO write
apply_incident_plan(incident, other, capability)      -> approval_rejected, NO write
apply_incident_plan(incident, action, capability)     -> write dispatched once
apply_incident_plan(incident, action, applied_cap)    -> approval_rejected, NO write
```

**What is enforced:** the MCP caller can request approval but cannot grant it.
The request response exposes only an opaque `approval_id`, never the capability
or idempotency key. A distinct operator command records the supplied approver
identity and yields a capability once; only its SHA-256 hash is stored. Validation
requires `approved` state, exact incident/action binding, and an unexpired grant.
`applied` and `expired` are terminal. Tests assert "no write was sent" against
observed HTTP traffic rather than the response's own claim.

**What is not enforced:** the lab does not authenticate the identity string
passed to `--approver`, protect the local JSON store from another local writer,
or provide a production policy service. The demo automates both caller and
operator roles for reproducibility, while exercising them through different
surfaces and processes. This proves structural separation, not human judgment.

Boundary: this is a workflow-layer control. It is not a Hermes policy control and
not an API-side authorization rule.

State machine:

```text
pending ──operator approve──> approved ──confirmed apply/replay──> applied
   │                            │
   └──────────── TTL ───────────┴────────────────────────────────> expired
```

An ambiguous failure does not transition `approved` to `applied`, because the
caller does not yet know whether the upstream committed. The same capability may
retry with the same idempotency key. If the upstream reports a replay, the
approval becomes `applied`; any further use is refused before dispatch.

## Idempotency and resume

The idempotency key is minted **with the approval**, not with the attempt, so a
resume reuses it automatically.

| Situation | Result |
|---|---|
| First approved call | `applied`, `replayed: false`, one record created |
| Same capability after a confirmed apply | `approval_rejected`, no request dispatched |
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

The operator identity is a caller-supplied fixture string, not an authenticated
principal. The approval store is a local JSON file and is not safe for concurrent
production writers, though it stores only the capability hash.

The audit log is append-only by convention — a plain `.jsonl` file with no
signature or chain hash. It is not tamper-evident, `run_started` and
`run_finished` carry a null correlation ID, and the enterprise API writes no
audit of its own, so a direct write to the API leaves no trace in it.
