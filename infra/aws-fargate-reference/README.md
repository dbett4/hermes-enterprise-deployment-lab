# AWS ECS/Fargate cloud/hybrid IaC reference

This directory is a **deployment blueprint**, not deployed infrastructure. It does not claim an AWS account, credentials, a successful cloud API call, running services, or a hard spend cap.

## What it plans (when `enable_deployment=true`)

Internal ALB (TLS only) in existing private subnets, a one-task/0.25-vCPU/0.5-GB ECS Fargate service for `enterprise-api`, encrypted EFS for demo action-store persistence with TLS/IAM access-point authorization, task IAM limited to EFS mount/write through that access point, execution IAM scoped to one existing ECR repository plus two existing Secrets Manager ARNs, known-empty ALB/EFS egress plus standalone directional security-group rules, CloudWatch logs/alarms, and an alert-only AWS Budget bounded by validation at USD 25.

Default `enable_deployment=false` plans **zero** managed resources.

## Customer-governed boundaries (not created here)

Bring your own VPC, private subnets, hybrid ingress path (VPN / Direct Connect / Transit Gateway / private connectivity as you already run it), ACM certificate, container image pull path, Secrets Manager secret *values*, Route53, ECR repository, NAT, public subnets, OIDC, WAF, and remote state backend. Those stay existing/customer-governed.

## Persistence and cost notes

EFS here is **single-region demo persistence**, not a production transactional database. ECS Container Insights stays off by default. The budget resource is **alert-only**, not hard enforcement. SNS email subscription confirmation and delivery are **unverified** by this blueprint.

## Offline proof (no apply)

From the repo root:

```bash
bash ./scripts/cloud-iac-proof.sh
```

That downloads repository-pinned OpenTofu 1.12.5 and the lock-file-pinned AWS provider, formats/validates, plans disabled and enabled graphs with dummy credentials and `-refresh=false`, asserts graph invariants, and writes `.cloud-iac-proof/receipt.json`. GitHub and the provider registry are contacted for those downloads; no AWS refresh or apply command runs. There is no `tofu apply` / `terraform apply` path.

The current repaired offline example plans 29 creates, zero changes, and zero destroys. The receipt separately records EFS IAM authorization, source-scoped task EFS policy, and known-empty ALB/EFS egress. Those fields do not prove container startup, AWS apply behavior, or runtime storage semantics.

A dedicated `cloud-iac-proof` GitHub Actions job runs the same contract and
uploads only the sanitized receipt and command log. Its status is evidence only
for the exact commit that ran; it never proves AWS apply, deployment, runtime,
or actual spend.

## Variables you must supply when enabling

| Variable | Role |
|---|---|
| `vpc_id` | Existing VPC |
| `private_subnet_ids` | ≥2 private subnets |
| `trusted_ingress_cidrs` | Hybrid/enterprise CIDRs allowed to the internal ALB |
| `acm_certificate_arn` | Existing ACM cert |
| `container_image` | Image ending in `@sha256:<64 hex>` |
| `ecr_repository_arn` | Existing private ECR repository that owns the image |
| `read_secret_arn` / `write_secret_arn` | Existing secret ARNs only |
| `notification_email` | Budget/alarm subscription target |

See `example.enabled.tfvars` for dummy placeholders used by the proof script.
