# How to check the claims

After installing the development and component requirements, run:

```bash
./scripts/proof.sh
```

It stops at the first failure and prints `LAB_PROOF_PASS` only after every check
below passes.

| Claim | Run | Pass condition | Limit |
|---|---|---|---|
| The MCP server is real and its tool list is scoped | FastMCP inspect plus the test suite | Four tools exist in write-enabled mode; the mutator is absent and uncallable in the default read/plan scope | CI checks the MCP protocol; the committed Hermes receipts show discovery, not model invocation |
| The mutating caller cannot mint its own approval capability | Approval and MCP tests | The first call returns only an opaque `approval_id`; the separate operator command records an identity and returns the plaintext capability once | The identity is a caller-supplied fixture string, not authenticated by an IdP |
| Capabilities are bound, expiring, and terminal | Approval-store and executor tests | Forged, cross-incident, cross-action, expired, and already-applied capabilities reject before POST | The local JSON store is not a transactional production authorization service |
| An ambiguous post-commit failure can resume without a duplicate side effect | `./scripts/demo.sh` and end-to-end tests | Injected 500 occurs after one commit; retry uses the same approval-scoped idempotency key and returns `replayed`; a third use rejects | Exactly-once is per approval/idempotency key, not globally per action |
| Credential scope fails closed | API, stdio-injection, and demo tests | Read scope receives 403; missing server credentials abort startup; a wrong explicitly injected token fails | Static fixture bearer tokens stand in for enterprise identity |
| A new checkout reproduces the result | Compose parse, 73 tests, demo, and the separate fresh-clone CI job | Configuration parses and the local checks complete without provider credentials | This script does not start containers, test Kubernetes, invoke a model, or represent production scale |

## CI

GitHub Actions runs the test matrix and a separate fresh-clone job on the public
repository. `scripts/fresh-clone-check.sh` can also check the committed revision
locally; it deliberately clones `HEAD`, so run it after committing changes.
