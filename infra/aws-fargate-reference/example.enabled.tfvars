# Offline plan example only. Dummy identifiers. No real account, secrets, or spend.
enable_deployment = true

aws_region     = "us-east-1"
aws_account_id = "123456789012"
name_prefix    = "hermes-lab-ref"

vpc_id = "vpc-0123456789abcdef0"
private_subnet_ids = [
  "subnet-0aaa1111bbbb2222c",
  "subnet-0ddd3333eeee4444f",
]
trusted_ingress_cidrs = [
  "10.10.0.0/16",
  "10.20.0.0/16",
]

acm_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-4000-8000-000000000000"

# Immutable digest reference (placeholder registry path; no ECR repository is created here).
container_image    = "123456789012.dkr.ecr.us-east-1.amazonaws.com/enterprise-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ecr_repository_arn = "arn:aws:ecr:us-east-1:123456789012:repository/enterprise-api"

read_secret_arn  = "arn:aws:secretsmanager:us-east-1:123456789012:secret:hermes-lab/read-fixture-AAAAAA"
write_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:hermes-lab/write-fixture-BBBBBB"

notification_email = "cloud-iac-alerts@example.invalid"

desired_count      = 1
task_cpu           = "256"
task_memory        = "512"
log_retention_days = 7
monthly_budget_usd = 25
