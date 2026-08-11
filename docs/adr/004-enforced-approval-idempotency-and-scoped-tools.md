# ADR 004: Add a write gate, idempotency, and server-side tool scoping

## Status

Accepted (2026-08-01); approval-control portion superseded by
[ADR 005](005-separated-operator-approval.md) on 2026-08-11. Superseded the approval and tool-surface sections of
[ADR 003](003-stdio-mcp-read-plan-tools.md), which remains accurate about the
stdio transport choice.

## Context

Reviewing the M3 lab exposed four missing controls and one broken credential
path:

1. **Approval was only metadata.** `approval_required: true` was a field
   in a receipt that nothing read. Every tool was read/plan-only, so there was
   no mutation for a gate to block.
2. **There was no idempotency, checkpoint, or resume path.** The strings `idempot`, `resume`,
   `retry`, and `checkpoint` did not occur anywhere in the repository. There was
   no run identity and no side effect that could be duplicated.
3. **Failure injection did not exercise recovery.** `?inject=error`
   returned a 500; nothing exercised recovering from it.
4. **The visible tool list was not actually filtered.** The Hermes
   config carried a `tools.include` list, but `hermes mcp test` reports the
   server's advertised tools, not the filtered agent-visible set. Narrowing the
   include list to one entry still printed all three tools. The shipped list
   (3 of 3) was a no-op that looked identical to an enforced one.
5. **Credentials never reached the MCP server.** `mcp.client.stdio.
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

`scripts/hermes-tool-filter-proof.sh` runs `hermes mcp test` against two server
configurations and shows Hermes discovering 4 tools, then 1.

### Append-only audit trail

`workflow_runner/audit.py` writes one JSON object per line, append-only, covering
tool invocation, approval request/grant/rejection, mutation attempt, commit,
replay, and failure — each with a correlation ID and a run ID.

## Consequences

Benefits:

- Approval, idempotency, resume, and scoping are implemented and tested.
- A wrong credential now fails, so the credential assertions mean something.
- The demo (`scripts/demo.sh`) is the same code path the tests exercise, so it
  cannot drift from reality.

Costs:

- The lab now has a mutable surface, small as it is, and a fixture reset endpoint
  that exists purely for determinism.
- The approval store and audit log are local files. They are adequate for a
  single-operator lab and would not survive multi-process contention.
- The gate lives in the workflow layer. It is not a Hermes policy control, and
  a different client that talked to the enterprise API directly with a write
  token would bypass it entirely.

## Limits at the time

- No model chooses or invokes these tools. Scripts and tests make every call. A
  model-driven run requires provider spend, which I declined on 2026-08-01. A
  real `hermes` build connects over stdio and lists the tools, but does not call
  them.
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
- At the time of this decision, the GitHub Actions workflow had not run because
  the repository had no remote. The public repository now runs CI.
