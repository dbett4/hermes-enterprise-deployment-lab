# Second-Operator Validation Protocol

This lab has **not** been validated by a second operator. This document is the
script for doing that; the results section below is intentionally blank and must
only be filled in by the person who actually ran the steps.

Purpose: establish that the claims in `README.md` hold on a machine that is not
the author's, from a clean clone, without help from the author.

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

You do **not** need Podman or Hermes for steps 1-4. They are only needed for
steps 5 and 6.

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

Record the exact summary line (for example `74 passed, 1 warning in 41.02s`).

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
| 3 | Applying without an approval token returns `pending_approval` and the store count does not change | STEP 4 |
| 4 | The injected post-commit fault returns `upstream_5xx` with resume instructions | STEP 5 |
| 5 | Resuming with the same token returns `replayed` | STEP 6 |
| 6 | The store contains exactly **one** record at the end | STEP 7 |
| 7 | A read-only credential gets `auth_failure` and creates no record | STEP 8 |
| 8 | The audit trail lists approval, failure, and replay events with correlation IDs | STEP 9 |

## Step 5 — containers (optional, needs Podman)

```bash
podman machine start          # if the default machine is stopped
podman compose up -d --build
curl -fsS localhost:8080/healthz
./scripts/smoke.sh
podman compose down -v
```

Note: `workflow-runner` is a run-to-completion container and exits 0 by design.
Some compose providers report that as a failure when `--wait` is used.

## Step 6 — Hermes discovery (optional, needs the Hermes CLI)

```bash
export ENTERPRISE_API_URL=http://127.0.0.1:8080
./scripts/hermes-mcp-proof.sh          # discovery receipt
./scripts/hermes-tool-filter-proof.sh  # differential scope proof
```

Both use an isolated `HERMES_HOME` under your temp directory and must not touch
`~/.hermes/config.yaml`. Confirm that:

- Hermes reports `Tools discovered: 3` for the default allowlist.
- The differential proof reports 4 tools for `all` and 1 tool for a single-tool
  allowlist.

## Step 7 — try to break it

Spend ten minutes trying to make the lab do something it claims it will not:

- Call `apply_incident_plan` with a token you invented.
- Call it with a token issued for a different `action_id`.
- Retry the approved call many times and re-check the store count.
- Set `ENTERPRISE_API_WRITE_TOKEN` to the read token and try to mutate.

Record anything that succeeded when it should not have.

---

## Results

**Status: NOT YET RUN.** Nobody other than the author has executed this protocol.

| Field | Value |
|---|---|
| Validator name | |
| Date (UTC) | |
| Commit SHA validated | |
| Environment (from the table above) | |
| Step 3 pytest summary line | |
| Step 4 demo result (pass/fail) | |
| Step 5 container result (pass/fail/skipped) | |
| Step 6 Hermes result (pass/fail/skipped) | |
| Step 7 findings | |
| Claims that did NOT hold | |
| Overall verdict | |

### Validator notes

_(free text — including anything confusing, under-documented, or wrong)_
