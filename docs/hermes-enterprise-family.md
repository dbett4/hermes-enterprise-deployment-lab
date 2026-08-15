# The two Hermes enterprise repositories

There are two, and they answer different halves of the same question. This page is
the single place that explains the split, so neither README has to carry the other's
story.

| | [Hermes Enterprise Evaluation Kit](https://github.com/dbett4/hermes-enterprise-evaluation-kit) | [Hermes Enterprise Deployment Lab](https://github.com/dbett4/hermes-enterprise-deployment-lab) (this repo) |
|---|---|---|
| Question | May this job run at all, under which approved configuration, and who owns the judgment? | When an agent can touch a system, can we scope it, approve it separately, and recover safely? |
| Layer | Governance and evidence around a run | Execution mechanics inside a run |
| Unit of work | A mission with a policy pack, a pinned configuration, an independent checker, and a receipt | A runbook action with a scoped tool surface, an operator grant, an idempotency key, and an audit trail |
| Hermes involvement | Pinned to Hermes v0.20.0 / `v2026.8.3`; one gated live one-shot exists | Hermes is an external MCP client used for tool discovery only |
| Default proof | `./scripts/proof.sh` — offline, credential-free | `./scripts/proof.sh` — host-native, credential-free |

## How they connect in code

The kit's S3 **Act** mission runs against this lab. Act is the archetype where a wrong
answer changes something, so its target has to have real controls rather than a prop
written for the demo:

- `scripts/deployment_lab_backend.py` (kit) resolves this repository from
  `HERMES_DEPLOYMENT_LAB` or a sibling directory, boots `enterprise-api` on a loopback
  port, and shuts it down afterwards.
- `scripts/deployment_lab_act_client.py` (kit) is an MCP client that calls this repo's
  own `propose_incident_plan` and `apply_incident_plan` tools over stdio, and grants
  approval through `workflow_runner.approval_operator`.
- The kit checks the run with `s3-approval-idempotency-oracle-h` and records this
  repository's path, commit, and the tools it called under `run_mode.deployment_lab`
  in the mission receipt.

Nothing about approval separation, idempotency, or resume is re-implemented on the kit
side. The kit selects a configuration, runs the mission, and checks the result; this
lab decides what the tool surface is and what happens when a write fails after commit.

The dependency runs one way. This repository does not import, require, or know about
the evaluation kit.

## What neither repository is

Neither is a customer Hermes Enterprise deployment, an official Nous product,
partnership, or endorsement, or a production identity/audit system. Both use fictional
organizations and synthetic incidents. Both are MIT licensed and built by
[Dave Bettner](https://davebettner.com).
