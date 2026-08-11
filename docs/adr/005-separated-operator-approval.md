# ADR 005: Separate approval request from operator grant

## Status

Accepted (2026-08-11). Supersedes the approval-control portion of
[ADR 004](004-enforced-approval-idempotency-and-scoped-tools.md). ADR 004 remains
the history of why the mutating tool, idempotency key, fault injection, scoped
surface, and fail-closed credentials exist.

## Context

The original two-call guard blocked the first mutation call but returned its
approval token to the same caller. It therefore proved a write barrier, not role
separation. The token never expired, `validate()` ignored lifecycle state, and
an applied token could dispatch again. Those gaps made the control too weak for
an enterprise-agent portfolio proof.

The lab must also preserve its strongest recovery property: when an upstream
commits and then returns a 5xx, the caller must be able to retry without creating
a second side effect.

## Decision

Use two different surfaces:

1. `apply_incident_plan` without a capability creates a `pending` request and
   returns only an opaque `approval_id`. The idempotency key and capability are
   not returned.
2. `python -m workflow_runner.approval_operator approve <approval_id>
   --approver <identity>` is the only grant path. It records the identity and
   returns a random capability once.
3. The JSON store persists only SHA-256 of the capability. Validation compares
   hashes in constant time and binds the grant to the exact incident and action.
4. Pending and approved requests expire after `APPROVAL_TTL_SECONDS` (900 by
   default). State is `pending`, `approved`, `applied`, or `expired`.
5. A confirmed apply or replay transitions the grant to terminal `applied`.
   Any later presentation is rejected before an HTTP write request is sent.
6. An upstream error leaves the grant `approved`. Retrying reuses the approval's
   idempotency key; a post-commit ambiguity resolves as an upstream replay and
   then transitions to `applied`.

## Consequences

Positive:

- The agent-facing MCP surface cannot self-approve; it never receives a secret
  until an operator grants the opaque request through another command.
- Approver identity, expiry, binding, and terminal behavior are machine-tested.
- Plaintext approval capabilities do not persist in the local store, logs, demo
  transcript, or MCP request response.
- Recovery after a post-commit failure remains safe and demonstrable.

Limitations:

- `--approver` records a supplied identity; the lab does not authenticate it.
- A local writer with access to `APPROVAL_STORE_PATH` can tamper with the store.
- The JSON file and in-process lock are for a single-host lab, not concurrent
  production operators.
- The workflow-layer gate can be bypassed by a client that holds the enterprise
  API write credential and calls the fixture API directly.

These limitations are stated in the README and architecture document. The lab
claims structural role separation and lifecycle enforcement, not production
identity assurance.
