# Independent AI-agent clean-checkout validation — 2026-08-15

**Verdict: PASS_WITH_LIMITATIONS**

This receipt records an independent AI-agent validator running the published
checklist from a brand-new checkout. It is **not human validation**, did not run
on a different physical machine, and does not replace the still-open human
second-operator gate.

## Identity and scope

| Field | Value |
|---|---|
| Validator | Independent Hermes subagent using Gemini 3.7 Flash; not the authoring agent and not a human |
| Checkout | Brand-new `/tmp/hedlab-validator/hedlab` clone from `https://github.com/dbett4/hermes-enterprise-deployment-lab.git` |
| Commit | `3da59385bcea7a0082b4f9280a5bcb22e6bd2196` (detached HEAD) |
| Date | 2026-08-15 UTC |
| OS | Ubuntu 24.04.4 LTS; Linux 6.8.0-137-generic |
| Architecture | x86_64 |
| Python | 3.12.3 in a new virtual environment |
| Hermes | Hermes Agent v0.20.0 (2026.8.3) |
| Container runtime | Docker CLI 29.7.1 present; daemon unavailable to the validator because `/var/run/docker.sock` was denied; no Podman |

The coordinator re-read the detached SHA and generated receipts, executed the
previously unfinished Hermes differential and adversarial checks, and stopped
the validator's temporary API process before publishing this document.

## Results

| Protocol step | Result | Verifiable marker or receipt |
|---|---|---|
| Clean clone | PASS | Detached `HEAD` was exactly `3da59385bcea7a0082b4f9280a5bcb22e6bd2196` |
| Install | PASS | New venv installed the three pinned requirement sets, including FastMCP 3.4.5 and pytest 8.3.4 |
| Full pytest | PASS | `240 passed, 2 warnings in 28.79s` |
| Deterministic demo | PASS | `DEMO PASSED - separated approval enforced, failure survived, exactly one side effect, capability terminal`; receipt `.smoke-receipts/demo-receipt.json` |
| Native telemetry | PASS | `TELEMETRY_PROOF_PASS prometheus=3.13.2 target=up alerts=5 outcomes=created,replayed,conflict,postcommit_error`; receipt `.telemetry-proof/receipt.json` |
| Native traces | PASS | `TRACE_PROOF_PASS endpoint=loopback-otlp-http events=pending,failed_resumable,replayed`; receipt `.trace-proof/receipt.json` |
| Container proof | SKIPPED | `CONTAINER_PROOF_FAIL: no usable docker/podman compose engine`; the validator could not access a daemon. This exact commit's separate public Docker-capable CI job passed in [Actions run 31891411678](https://github.com/dbett4/hermes-enterprise-deployment-lab/actions/runs/31891411678). |
| Hermes default discovery | PASS | `Tools discovered: 3`; receipt `.mcp-receipts/hermes-mcp-proof.json` |
| Hermes differential filter | PASS | Full server allowlist produced `Tools discovered: 4`; single-tool allowlist produced `Tools discovered: 1`; receipt `.mcp-receipts/hermes-tool-filter-proof.json` |
| Adversarial checks | PASS | The selected clean-checkout tests for forged capability, unknown/different action, approval-ID misuse, terminal replay, expiry, duplicate-action convergence, and read-token mutation all passed |

## Commands executed

```bash
git clone https://github.com/dbett4/hermes-enterprise-deployment-lab.git hed-lab
git -C hed-lab checkout --detach 3da59385bcea7a0082b4f9280a5bcb22e6bd2196
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt \
  -r workflow-runner/requirements.txt \
  -r enterprise-mcp/requirements.txt
.venv/bin/python -m pytest -q
cp .env.example .env
./scripts/demo.sh
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/telemetry-proof.sh
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/trace-proof.sh
bash ./scripts/container-proof.sh
./scripts/hermes-mcp-proof.sh
./scripts/hermes-tool-filter-proof.sh
```

The coordinator then ran the named tests corresponding to every Step 8 attack
class in `workflow-runner/tests/test_executor.py` and
`enterprise-mcp/tests/test_approval_and_resume_over_mcp.py`; all selected tests
passed.

## Adversarial findings

No attempted forbidden behavior succeeded:

- an invented capability dispatched nothing;
- a capability bound to another incident or action was refused;
- an opaque approval ID could not be used as a capability;
- a confirmed apply was terminal and did not dispatch another mutation;
- expired pending requests and capabilities were rejected without a write;
- a read-only credential could not mutate;
- distinct approvals for the same incident/action pair converged on one side effect.

## Limitations

- This was an independent AI-agent lane on the same VPS, not a human reviewer or
  a different physical machine.
- The validator could not execute the container proof locally because it had no
  usable Docker/Podman daemon. The linked public CI container result is separate
  evidence, not a substitute silently folded into this receipt.
- Hermes was used only for MCP discovery and enumeration. No model invoked a
  tool, and no model-driven mutation is claimed.
- All data, identities, services, telemetry, traces, and failures were synthetic
  and local. No production identity, cloud apply, customer tenant, or external
  mutation was involved.

## Conclusion

The independently executed clean-checkout paths that the environment permitted
all held. The result is therefore **PASS_WITH_LIMITATIONS**, with human
second-operator and validator-local container execution still open.