# ADR 003: Use stdio MCP for the read and planning tools

## Status

Accepted (M3)

## Context

The lab needed a small integration surface that Hermes could discover. It also
needed to be easy to run locally and safe to publish. The requirements were:

- Prove real MCP protocol discovery and tool calls (not only a standalone Python client).
- Keep consequential actions approval-gated and never execute external mutations.
- Avoid leaking fixture tokens or customer data through tool output, logs, or receipts.
- Run locally in Compose without paid services or live Hermes profile mutation.

I considered three approaches:

1. **Native Hermes plugin** — tight coupling to Hermes release cadence; harder for portfolio reviewers to exercise without the full agent runtime.
2. **Generic HTTP MCP proxy** — too broad; least privilege would be difficult to
   explain and enforce.
3. **FastMCP stdio server** — standard MCP transport; explicit tool list; reuses existing `workflow-runner` client and planner.

## Decision

Expose exactly three FastMCP stdio tools backed by the existing enterprise API client and planner:

| Tool | Purpose |
|---|---|
| `check_enterprise_api` | Health/readiness probe with correlation ID; never returns token |
| `get_incident_context` | Incident + runbook retrieval with dependency-call evidence |
| `propose_incident_plan` | Structured receipt with `approval_required: true` for consequential steps |

Configuration is environment-driven (`ENTERPRISE_API_URL`, `${ENTERPRISE_API_TOKEN}`, timeout). Hermes connects via stdio with an isolated `HERMES_HOME` example config. Token values are never committed; Hermes resolves `${ENTERPRISE_API_TOKEN}` at connect time.

The checks are split in two:

1. **FastMCP protocol** — `fastmcp inspect/list/call` and unit tests exercise the
   tools.
2. **Hermes discovery** — `hermes mcp test enterprise_ops` runs with an isolated
   `HERMES_HOME` and lists the tools. Deterministic Hermes invocation would need
   an LLM, so it is not part of this lab.

## Consequences

Benefits:

- The real Hermes CLI discovers the server when it is installed locally.
- Tool surface is fixed and auditable; no generic HTTP escape hatch.
- Same correlation and error classification as the workflow runner.

Costs:

- Stdio MCP requires `PYTHONPATH` or packaging discipline for `workflow-runner` imports.
- CI must install FastMCP and run MCP protocol tests in addition to unit tests.
- Live Hermes proof depends on the `hermes` CLI on the operator machine; CI runs protocol-only smoke (`MCP_SMOKE_PROTOCOL_ONLY=1`).

## Later work

M4 adds cross-layer correlation metrics and failure-injection scripts spanning API, MCP, and receipts.
