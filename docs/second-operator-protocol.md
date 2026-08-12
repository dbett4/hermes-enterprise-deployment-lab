# Second-operator checklist

**Status: not run. No validator is assigned or scheduled.**

This is the checklist I would give an independent reviewer. It is not a
validation result. The results table stays blank until someone other than the
author runs every step on a different machine and records what happened.

The goal is to see whether the claims in `README.md` hold from a clean clone,
without help from the author.

## Before you start

Record these, because they are the variables most likely to explain a difference:

| Item | Value |
|---|---|
| OS and version | |
| CPU architecture | |
| Python version (`python3 -V`) | |
| Podman version (`podman --version`) or "not installed" | |
| Hermes CLI version (`hermes --version`) or "not installed" | |
| Date (UTC) | |

You do **not** need Docker, Podman, or Hermes for steps 1-5. The native telemetry
step needs `curl`, `sha256sum`, `tar`, and release-download network access.
The native trace step needs `curl` and stays on loopback.
Docker or Podman is needed for step 6; Hermes is needed for step 7.

## Step 1 — clean clone

```bash
git clone <repo-url> hermes-lab-validation
cd hermes-lab-validation
git rev-parse HEAD
```

Record the commit SHA. Everything below refers to that commit.

## Step 2 — install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt \
                      -r workflow-runner/requirements.txt \
                      -r enterprise-mcp/requirements.txt
```

## Step 3 — test suite

```bash
.venv/bin/python -m pytest -q
```

Record the exact summary line; do not copy a count from repository prose.

## Step 4 — the demo

```bash
cp .env.example .env
./scripts/demo.sh
```

Check each of these in the printed transcript, and mark anything that differs:

| # | Claim | Where to look |
|---|---|---|
| 1 | The read/plan allowlist does not expose `apply_incident_plan`, and calling it fails | STEP 1 |
| 2 | The write-enabled allowlist exposes exactly four tools | STEP 2 |
| 3 | Applying without a capability returns `pending_approval`, only an opaque `approval_id`, and the store count does not change | STEP 4 |
| 4 | A separate operator command records `demo-operator@example.com`; the capability is redacted and its plaintext is not stored | STEP 5 |
| 5 | The injected post-commit fault returns `upstream_5xx` with resume instructions | STEP 6 |
| 6 | Resuming with the same capability returns `replayed` | STEP 7 |
| 7 | The store contains exactly **one** record and a later capability use is rejected as `approval_already_applied` | STEP 8 |
| 8 | A read-only credential gets `auth_failure` and creates no record | STEP 9 |
| 9 | The audit trail lists request, named grant, capability acceptance, failure, and replay events | STEP 10 |

## Step 5 — native telemetry and traces

```bash
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/telemetry-proof.sh
python3 -m json.tool .telemetry-proof/receipt.json
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/trace-proof.sh
python3 -m json.tool .trace-proof/receipt.json
```

Require target `up`, five loaded alerts, positive fixtures for every alert, the
idle-latency negative control, and all three action outcomes. Confirm no proof
`uvicorn` or Prometheus process remains afterward. This is native process
evidence, not a container or external-pager result.

The trace proof must show direct workflow CLIENT→API SERVER causality: the
SERVER span shares the W3C trace ID and has `parent_span_id == client.span_id`.
It must also show bounded pending/failure/resume events. Captured bytes and the receipt
must not contain fixture tokens, capabilities, idempotency keys, notes, or
bodies. Confirm the OTLP capture process is gone afterward. This is sampled
local capture, not a collector backend.

## Step 6 — containers (optional, needs Docker or Podman Compose)

```bash
bash ./scripts/container-proof.sh
```

Expect `CONTAINER_PROOF_PASS`. Receipts land under `.container-proof/`.

The proof tears its isolated Compose project down before writing a pass receipt.
It leaves no API on port 8080; Step 7 starts a separate manual stack.

## Step 7 — Hermes discovery (optional, needs the Hermes CLI)

```bash
(
  set -e
  trap 'docker compose down -v --remove-orphans' EXIT
  docker compose up -d --build enterprise-api prometheus
  export ENTERPRISE_API_URL=http://127.0.0.1:8080
  ./scripts/hermes-mcp-proof.sh          # discovery receipt
  ./scripts/hermes-tool-filter-proof.sh  # differential scope proof
)
```

Both use an isolated `HERMES_HOME` under your temp directory and must not touch
`~/.hermes/config.yaml`. Confirm that:

- Hermes reports `Tools discovered: 3` for the default allowlist.
- The differential proof reports 4 tools for `all` and 1 tool for a single-tool
  allowlist.

If you already have a separate Compose stack up, the older read/plan smoke is
`./scripts/smoke.sh`. `workflow-runner` is a run-to-completion container and exits
0 by design; some Compose providers report that as a failure under `--wait`.

## Step 8 — try to break it

Spend ten minutes trying to make the lab do something it claims it will not:

- Call `apply_incident_plan` with a capability you invented.
- Call it with a capability granted for a different `action_id`.
- Try to use an opaque `approval_id` as if it were a capability.
- Retry after a confirmed apply and verify no new HTTP mutation is dispatched.
- Set `APPROVAL_TTL_SECONDS=1`, let a grant expire, and try to use it.
- Set `ENTERPRISE_API_WRITE_TOKEN` to the read token and try to mutate.

Record anything that succeeded when it should not have.

---

## Results

Not run. No validator has been identified or scheduled.

| Field | Value |
|---|---|
| Validator name | |
| Date (UTC) | |
| Commit SHA validated | |
| Environment (from the table above) | |
| Step 3 pytest summary line | |
| Step 4 demo result (pass/fail) | |
| Step 5 native telemetry result (pass/fail) | |
| Step 5 native trace result (pass/fail) | |
| Step 6 container result (pass/fail/skipped) | |
| Step 7 Hermes result (pass/fail/skipped) | |
| Step 8 findings | |
| Claims that did NOT hold | |
| Overall verdict | |

### Validator notes

_(free text — including anything confusing, under-documented, or wrong)_
