output "enabled" {
  description = "Whether this plan manages resources (enable_deployment)."
  value       = var.enable_deployment
}

output "ecs_cluster_name" {
  description = "ECS cluster name when enabled; null otherwise."
  value       = var.enable_deployment ? aws_ecs_cluster.this[0].name : null
}

output "ecs_service_name" {
  description = "ECS service name when enabled; null otherwise."
  value       = var.enable_deployment ? aws_ecs_service.api[0].name : null
}

output "alb_dns_name" {
  description = "Internal ALB DNS name when enabled; null otherwise."
  value       = var.enable_deployment ? aws_lb.api[0].dns_name : null
}

output "efs_file_system_id" {
  description = "Encrypted EFS id for demo action-store persistence when enabled; null otherwise."
  value       = var.enable_deployment ? aws_efs_file_system.actions[0].id : null
}

output "log_group_name" {
  description = "CloudWatch log group name when enabled; null otherwise."
  value       = var.enable_deployment ? aws_cloudwatch_log_group.api[0].name : null
}

output "budget_name" {
  description = "Alert-only monthly budget name when enabled; null otherwise."
  value       = var.enable_deployment ? aws_budgets_budget.monthly[0].name : null
}

output "evidence_boundary" {
  description = "Static reminder that this tree is a blueprint, not a deployment."
  value = {
    deployed          = false
    cloud_api_apply   = false
    spend_enforcement = "alert-only"
    persistence_scope = "single-region-demo-not-production-db"
    network_boundary  = "customer-governed-existing-vpc-subnets-hybrid-path"
  }
}
