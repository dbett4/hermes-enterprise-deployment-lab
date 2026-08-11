# Claim-to-command proof map

Run the credential-free packet after installing the development and component
requirements:

```bash
./scripts/proof.sh
```

It exits nonzero on the first failed oracle and prints `LAB_PROOF_PASS` only
after all rows below pass.

| Claim | Native proof | Oracle | Boundary |
|---|---|---|---|
| The MCP surface is real and explicitly scoped | FastMCP inspect plus the test suite | Four tools exist in write-enabled mode; the mutator is absent and uncallable in the default read/plan scope | CI uses protocol-only proof; the committed Hermes receipts prove discovery/enumeration, not model invocation |
| The mutating caller cannot mint its own approval capability | Approval and MCP tests | The first call returns only an opaque `approval_id`; the separate operator command records an identity and returns the plaintext capability once | The identity is a caller-supplied fixture string, not authenticated by an IdP |
| Capabilities are bound, expiring, and terminal | Approval-store and executor tests | Forged, cross-incident, cross-action, expired, and already-applied capabilities reject before POST | The local JSON store is not a transactional production authorization service |
| An ambiguous post-commit failure can resume without a duplicate side effect | `./scripts/demo.sh` and end-to-end tests | Injected 500 occurs after one commit; retry uses the same approval-scoped idempotency key and returns `replayed`; a third use rejects | Exactly-once is per approval/idempotency key, not globally per action |
| Credential scope fails closed | API, stdio-injection, and demo tests | Read scope receives 403; missing server credentials abort startup; a wrong explicitly injected token fails | Static fixture bearer tokens stand in for enterprise identity |
| The topology and operations surface are reproducible | Compose parse, 73 tests, demo, and the separate fresh-clone CI job | Configuration parses and the local proof completes without provider credentials | This packet does not start containers, prove Kubernetes, invoke a model, or represent production scale |

## Publication gate

The repository may claim a configured CI workflow before publication, but it
must not claim a green remote run until GitHub Actions has executed successfully
on the public commit. `scripts/fresh-clone-check.sh` proves the local clone half
only and deliberately runs after the changes have been committed.
