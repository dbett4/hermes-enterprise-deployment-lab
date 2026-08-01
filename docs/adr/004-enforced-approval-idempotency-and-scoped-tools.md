# ADR 004: Enforced two-phase mutation guard, idempotency, and a scoped tool surface

## Status

Accepted (2026-08-01). Supersedes the approval and tool-surface sections of
[ADR 003](003-stdio-mcp-read-plan-tools.md), which remains accurate about the
stdio transport choice.

## Context

A review of the M3 lab found that four of the properties the repository implied
were not actually implemented, and one that was implemented was broken:

1. **Approval was metadata, not a gate.** `approval_required: true` was a field
   in a receipt that nothing read. Every tool was read/plan-only, so there was
   no mutation for a gate to block.
2. **No idempotency, checkpoint, or resume.** The strings `idempot`, `resume`,
   `retry`, and `checkpoint` did not occur anywhere in the repository. There was
   no run identity and no side effect that could be duplicated.
3. **Failure injection existed but proved nothing about recovery.** `?inject=error`
   returned a 500; nothing exercised recovering from it.
4. **Tool filtering was not enforced anywhere the lab could observe.** The Hermes
   config carried a `tools.include` list, but `hermes mcp test` reports the
   server's advertised tools, not the filtered agent-visible set. Narrowing the
   include list to one entry still printed all three tools. The shipped list
   (3 of 3) was a no-op that looked identical to an enforced one.
5. **Credentials were never injected into the MCP server.** `mcp.client.stdio.
   get_default_environment()` forwards only `HOME`, `LOGNAME`, `PATH`, `SHELL`,
   `USER`. The smoke script ran the server through the fastmcp CLI's
   command-spawning subcommands, which cannot pass `env`, so `ENTERPRISE_API_TOKEN`
   never arrived and the server silently fell back to a `"lab-read-token"` default
   hardcoded in `enterprise_mcp/config.py`. A smoke run with a deliberately wrong
   token still SUCCEEDED, and the smoke's own `"lab-read-token" not in output`
   assertion passed trivially.

## Decision

### Credentials fail closed and are passed explicitly

`load_settings()` has no default token; a missing `ENTERPRISE_API_TOKEN` raises
`ConfigurationError` and `main()` exits 2 before serving. Clients build the
server's full environment themselves (`env=` on `StdioTransport`, or the Hermes
`env:` block). The protocol smoke includes a negative control — a wrong token
must produce `auth_failure` — which the previous smoke was structurally unable
to fail.

### Two token scopes

`ENTERPRISE_API_TOKEN` (read) and `ENTERPRISE_API_WRITE_TOKEN` (write) are
different values. `POST /v1/incidents/{id}/actions` requires write scope, so
"a read-only credential cannot mutate" is a property of the API, not a promise
in prose.

### One mutating tool behind a two-phase guard

`apply_incident_plan` is the only tool that changes anything.

| Call | Effect |
|---|---|
| without `approval_token` | mints a token, **sends no write request**, returns `pending_approval` |
| with an unknown/forged token | no write request, returns `approval_rejected` |
| with a token bound to another incident or action | no write request, returns `approval_rejected` |
| with the matching token | performs the write |

What this enforces is exactly one thing: a single call can never mutate; a
mutation requires a second call carrying a token minted by the first call's
refusal. The unit tests assert "no write happened" against observed HTTP traffic,
not against the response's own claim.

**It is not a human-in-the-loop control, and this ADR does not claim it is.** The
token is returned to the same caller that was refused, so an autonomous caller
can self-approve immediately — `scripts/demo.sh` and every test do precisely
that. No approver identity is recorded, there is no out-of-band channel, no
expiry, and no second-party check. A real approval control was deferred by the
owner on 2026-08-01; the four changes it would require are enumerated under
"Roadmap — what a real approval control would require" in
[`docs/architecture.md`](../architecture.md).

*(Correction, 2026-08-01: this section previously read "Supplying the token is
the human step." That was false as written and is retained here only to record
what changed.)*

### Idempotency is minted with the approval, not with the attempt

Each approval carries an `idempotency_key`. The API stores side effects keyed by
it: a repeat returns the original record with `replayed: true`. Because the key
belongs to the approval, a resume after a failure reuses it automatically — the
caller does not have to remember to.

### A fault that commits and then fails

`?inject=error` fails *before* commit; `?inject=error_after_commit` commits and
*then* returns 500. The second is the case where a naive retry double-applies,
so it is the one the resume proof uses. The MCP server reads the fault switch
from `ENTERPRISE_INJECT_FAILURE`, so the fault is set in configuration rather
than through a tool argument.

### The tool surface is filtered at registration

`ENTERPRISE_MCP_ENABLED_TOOLS` selects which tools are registered on the FastMCP
server. Excluded tools are absent from `list_tools` and not callable. The default
is read/plan only; the mutating tool is opt-in. An unknown name in the list is a
configuration error rather than a silently ignored entry.

This is enforcement the lab can prove, and — importantly — that Hermes can
observe: `scripts/hermes-tool-filter-proof.sh` runs `hermes mcp test` against two
configs and shows Hermes discovering 4 tools versus 1.

### Append-only audit trail

`workflow_runner/audit.py` writes one JSON object per line, append-only, covering
tool invocation, approval request/grant/rejection, mutation attempt, commit,
replay, and failure — each with a correlation ID and a run ID.

## Consequences

**Positive**

- Approval, idempotency, resume, and scoping are executable claims with tests.
- A wrong credential now fails, so the credential assertions mean something.
- The demo (`scripts/demo.sh`) is the same code path the tests exercise, so it
  cannot drift from reality.

**Negative**

- The lab now has a mutable surface, small as it is, and a fixture reset endpoint
  that exists purely for determinism.
- The approval store and audit log are local files. They are adequate for a
  single-operator lab and would not survive multi-process contention.
- The gate lives in the workflow layer. It is not a Hermes policy control, and
  a different client that talked to the enterprise API directly with a write
  token would bypass it entirely.

## What this ADR still does not claim

- **No model has chosen or invoked any of these tools, and none will.** Every
  invocation in this repository is made by a script or a test. A model-driven run
  requires provider spend, which the owner declined on 2026-08-01. This is a
  permanent limit, not a pending task. The Hermes involvement that is real is
  discovery: a real `hermes` build connects over stdio and enumerates the tools.
- **The guard is two-call, not two-party.** Nothing requires, records, or can
  require a human.
- The approval store is an unauthenticated JSON file; anything that can write
  `APPROVAL_STORE_PATH` can plant a token the guard accepts.
- `validate()` does not read `status`, so a token is never consumed or expired.
- "Exactly once" is per approval, not per action: two approvals for the same
  `action_id` carry two idempotency keys and produce two records.
- The audit log is append-only by convention, not tamper-evident, and the
  enterprise API keeps no audit of its own.
- Hermes's own `tools.include` enforcement against a model is unproven here.
- The GitHub Actions workflow has never executed; the repository has no remote.
