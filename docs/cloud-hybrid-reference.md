# Cloud/hybrid IaC reference

The Enterprise Deployment Lab includes a statically executable AWS ECS/Fargate **deployment blueprint** under `infra/aws-fargate-reference/`. It is not a cloud deployment.

## Evidence boundary

`scripts/cloud-iac-proof.sh` proves OpenTofu format/validate, a disabled plan with zero managed resources, and an enabled offline plan built with dummy credentials, provider skip flags, and `-refresh=false`. It downloads OpenTofu and provider packages from GitHub/the provider registry, but invokes no AWS refresh or apply path. It does **not** claim an AWS account, deployed infrastructure, Docker-backed runtime evidence, external notification delivery, or a hard spend cap.

Pass line shape:

```text
CLOUD_IAC_PROOF_PASS ... deployed=false aws_refresh=false apply=false
```

Receipt: `.cloud-iac-proof/receipt.json` (no credentials or secret values).

The workflow defines a dedicated GitHub Actions `cloud-iac-proof` job with
read-only repository permissions. Its per-commit status and sanitized uploaded
receipt are the CI authority. Even when green, it is validation evidence—not an
apply, deployment, runtime, or spend claim.

## Architecture in one paragraph

You supply an existing VPC, at least two private subnets, trusted ingress CIDRs for an already-governed enterprise/hybrid path, an ACM certificate ARN, an immutable container image digest plus its existing ECR repository ARN, and Secrets Manager ARNs for read/write fixture credentials. When `enable_deployment=true`, the plan adds an ECS cluster (Container Insights off by default), an internal ALB with HTTPS only, exactly one 0.25-vCPU/0.5-GB Fargate task without a public IP, encrypted EFS demo persistence with TLS and IAM access-point authorization, a task role limited to EFS mount/write through that access point, dedicated execution IAM for logs/secrets/image pull, known-empty ALB/EFS security-group egress plus standalone directional rules, three ALB alarms, and an alert-only monthly budget validated at no more than USD 25.

The image and ECS task both default to UID/GID 65534, and enabled input validation rejects root UID `0`. This is source/plan evidence only; image startup under read-only-root constraints remains unverified until a Docker-capable runtime proof succeeds.

## Explicitly out of scope

This reference does **not** create a VPC, NAT gateway, public subnet, Route53 zone, VPN, Direct Connect, Transit Gateway, ECR repository, secret values, or a remote state backend. Those remain existing/customer-governed boundaries.

It also does not claim customer VPN wiring, OIDC, WAF, external paging, production storage, or multi-region availability.

## Cost and alerting honesty

AWS Budgets notifications are **alert-only**, not hard enforcement. Optional SNS email for alarms requires subscription confirmation; confirmation and delivery are **unverified** here. EFS is labeled as single-region demo persistence, not a production transactional database.

## Pins

- OpenTofu `1.12.5` (repository SHA-256 pins for linux amd64/arm64; three-way equality with upstream SHA256SUMS)
- hashicorp/aws provider `6.58.0`, with Linux amd64/arm64 package hashes in `.terraform.lock.hcl`

## Related local evidence

Compose/container and native telemetry/trace proofs remain separate. This cloud IaC tree does not replace or weaken those boundaries.
