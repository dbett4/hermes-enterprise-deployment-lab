variable "enable_deployment" {
  description = "When false (default), this module plans zero managed resources. Set true only for offline plan review of the enabled graph."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region for the reference. Single-region demo only."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "Twelve-digit AWS account id used only for the alert-only budget resource. Not looked up via STS."
  type        = string
  default     = "000000000000"

  validation {
    condition = (
      can(regex("^[0-9]{12}$", var.aws_account_id)) &&
      (!var.enable_deployment || var.aws_account_id != "000000000000")
    )
    error_message = "aws_account_id must be a twelve-digit non-placeholder account id when enable_deployment is true."
  }
}

variable "name_prefix" {
  description = "Prefix for resource names in this lab reference."
  type        = string
  default     = "hermes-lab"
}

variable "vpc_id" {
  description = "Existing customer-governed VPC id. This reference does not create a VPC."
  type        = string
  default     = ""

  validation {
    condition     = !var.enable_deployment || can(regex("^vpc-([0-9a-f]{8}|[0-9a-f]{17})$", var.vpc_id))
    error_message = "vpc_id must be an existing VPC id when enable_deployment is true."
  }
}

variable "private_subnet_ids" {
  description = "At least two existing private subnet ids across AZs."
  type        = list(string)
  default     = []

  validation {
    condition = (
      !var.enable_deployment || (
        length(var.private_subnet_ids) >= 2 &&
        length(distinct(var.private_subnet_ids)) == length(var.private_subnet_ids) &&
        alltrue([for subnet_id in var.private_subnet_ids : can(regex("^subnet-([0-9a-f]{8}|[0-9a-f]{17})$", subnet_id))])
      )
    )
    error_message = "private_subnet_ids must contain at least two distinct existing subnet ids when enable_deployment is true."
  }
}

variable "trusted_ingress_cidrs" {
  description = "CIDRs already admitted by the enterprise/hybrid path. ALB HTTP/TLS ingress is limited to these."
  type        = list(string)
  default     = []

  validation {
    condition = (
      !var.enable_deployment || (
        length(var.trusted_ingress_cidrs) > 0 &&
        !contains(var.trusted_ingress_cidrs, "0.0.0.0/0") &&
        !contains(var.trusted_ingress_cidrs, "::/0") &&
        alltrue([for cidr in var.trusted_ingress_cidrs : can(cidrhost(cidr, 0))])
      )
    )
    error_message = "trusted_ingress_cidrs must contain valid CIDRs and must not include 0.0.0.0/0 or ::/0."
  }
}

variable "acm_certificate_arn" {
  description = "Existing ACM certificate ARN for the internal HTTPS listener."
  type        = string
  default     = ""

  validation {
    condition     = !var.enable_deployment || can(regex("^arn:aws[a-zA-Z-]*:acm:[a-z0-9-]+:[0-9]{12}:certificate/[A-Za-z0-9-]+$", var.acm_certificate_arn))
    error_message = "acm_certificate_arn must be an existing ACM certificate ARN when enable_deployment is true."
  }
}

variable "container_image" {
  description = "Immutable container image reference ending in @sha256:<64 hex>."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_deployment ||
      can(regex("@sha256:[0-9a-f]{64}$", var.container_image))
    )
    error_message = "container_image must end with @sha256: followed by 64 lowercase hex characters when enable_deployment is true."
  }
}

variable "ecr_repository_arn" {
  description = "Existing private ECR repository ARN that owns container_image. Used only to scope image-layer reads."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_deployment ||
      can(regex("^arn:aws[a-zA-Z-]*:ecr:[a-z0-9-]+:[0-9]{12}:repository/[A-Za-z0-9._/-]+$", var.ecr_repository_arn))
    )
    error_message = "ecr_repository_arn must be an existing private ECR repository ARN when enable_deployment is true."
  }
}

variable "read_secret_arn" {
  description = "Existing Secrets Manager ARN for the read fixture credential. ARN only; no secret value."
  type        = string
  default     = ""

  validation {
    condition     = !var.enable_deployment || can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.read_secret_arn))
    error_message = "read_secret_arn must be an existing Secrets Manager ARN when enable_deployment is true."
  }
}

variable "write_secret_arn" {
  description = "Existing Secrets Manager ARN for the write fixture credential. ARN only; no secret value."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_deployment || (
        can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.write_secret_arn)) &&
        var.read_secret_arn != var.write_secret_arn
      )
    )
    error_message = "write_secret_arn must be a distinct existing Secrets Manager ARN when enable_deployment is true."
  }
}

variable "notification_email" {
  description = "Email for budget and alarm SNS subscriptions. Confirmation and delivery are unverified by this blueprint."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_deployment ||
      (length(var.notification_email) > 3 && can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email)))
    )
    error_message = "notification_email must be a valid subscription address when enable_deployment is true."
  }
}

variable "desired_count" {
  description = "ECS service desired count. This evaluation reference is fixed at one task."
  type        = number
  default     = 1

  validation {
    condition     = var.desired_count == 1
    error_message = "This cost-bounded evaluation reference intentionally supports exactly one task."
  }
}

variable "task_cpu" {
  description = "Fargate task CPU units. This evaluation reference is fixed at 256 (0.25 vCPU)."
  type        = string
  default     = "256"

  validation {
    condition     = var.task_cpu == "256"
    error_message = "This cost-bounded evaluation reference fixes task_cpu at 256."
  }
}

variable "task_memory" {
  description = "Fargate task memory in MiB. This evaluation reference is fixed at 512 (0.5 GB)."
  type        = string
  default     = "512"

  validation {
    condition     = var.task_memory == "512"
    error_message = "This cost-bounded evaluation reference fixes task_memory at 512 MiB."
  }
}

variable "container_user" {
  description = "Non-root numeric user matching image capabilities (debian slim nobody)."
  type        = string
  default     = "65534"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.container_user))
    error_message = "container_user must be a non-root numeric uid."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days, bounded at no more than 30."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 5, 7, 14, or 30."
  }
}

variable "monthly_budget_usd" {
  description = "Alert-only AWS Budgets monthly amount in USD, bounded at no more than 25. Notifications are not hard enforcement."
  type        = number
  default     = 25

  validation {
    condition     = var.monthly_budget_usd > 0 && var.monthly_budget_usd <= 25
    error_message = "This evaluation reference requires a positive monthly budget no greater than USD 25."
  }
}

variable "additional_tags" {
  description = "Optional extra tags merged with the required reference tags."
  type        = map(string)
  default     = {}
}

variable "enable_container_insights" {
  description = "ECS Container Insights. Default false as a cost boundary."
  type        = bool
  default     = false
}
