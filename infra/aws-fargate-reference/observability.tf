resource "aws_cloudwatch_log_group" "api" {
  count = var.enable_deployment ? 1 : 0

  name              = "/ecs/${var.name_prefix}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_sns_topic" "alerts" {
  count = var.enable_deployment ? 1 : 0

  name = "${var.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count = var.enable_deployment ? 1 : 0

  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  count = var.enable_deployment ? 1 : 0

  alarm_name          = "${var.name_prefix}-alb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Internal ALB 5xx count. SNS confirmation and delivery are unverified by this blueprint."
  alarm_actions       = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    LoadBalancer = aws_lb.api[0].arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "target_response_time" {
  count = var.enable_deployment ? 1 : 0

  alarm_name          = "${var.name_prefix}-target-response-time"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 2
  treat_missing_data  = "notBreaching"
  alarm_description   = "Target response time. SNS confirmation and delivery are unverified by this blueprint."
  alarm_actions       = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    LoadBalancer = aws_lb.api[0].arn_suffix
    TargetGroup  = aws_lb_target_group.api[0].arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "healthy_host_count" {
  count = var.enable_deployment ? 1 : 0

  alarm_name          = "${var.name_prefix}-healthy-host-count"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_description   = "Healthy host count below desired. SNS confirmation and delivery are unverified by this blueprint."
  alarm_actions       = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    LoadBalancer = aws_lb.api[0].arn_suffix
    TargetGroup  = aws_lb_target_group.api[0].arn_suffix
  }
}
