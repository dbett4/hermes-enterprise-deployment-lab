locals {
  enabled     = var.enable_deployment
  name_prefix = var.name_prefix
  trusted_ipv4_cidrs = toset([
    for cidr in var.trusted_ingress_cidrs : cidr if !strcontains(cidr, ":")
  ])
  trusted_ipv6_cidrs = toset([
    for cidr in var.trusted_ingress_cidrs : cidr if strcontains(cidr, ":")
  ])
  # Single-region demo persistence only. Not a production transactional database.
  efs_demo_label = "single-region-demo-persistence-not-production-db"

  container_secrets = [
    {
      name      = "ENTERPRISE_API_TOKEN"
      valueFrom = var.read_secret_arn
    },
    {
      name      = "ENTERPRISE_API_WRITE_TOKEN"
      valueFrom = var.write_secret_arn
    },
  ]

  container_environment = [
    {
      name  = "ACTION_STORE_PATH"
      value = "/var/lib/enterprise-api/actions.json"
    },
  ]
}

resource "aws_security_group" "alb" {
  count = local.enabled ? 1 : 0

  name        = "${local.name_prefix}-alb"
  description = "Internal ALB: HTTPS from trusted hybrid CIDRs only"
  vpc_id      = var.vpc_id
  egress      = []
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = local.enabled ? local.trusted_ipv4_cidrs : toset([])

  security_group_id = aws_security_group.alb[0].id
  description       = "HTTPS from a trusted enterprise/hybrid IPv4 CIDR"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https_ipv6" {
  for_each = local.enabled ? local.trusted_ipv6_cidrs : toset([])

  security_group_id = aws_security_group.alb[0].id
  description       = "HTTPS from a trusted enterprise/hybrid IPv6 CIDR"
  cidr_ipv6         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  count = local.enabled ? 1 : 0

  security_group_id            = aws_security_group.alb[0].id
  referenced_security_group_id = aws_security_group.tasks[0].id
  description                  = "Internal ALB to ECS tasks on the application port"
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "tasks" {
  count = local.enabled ? 1 : 0

  name        = "${local.name_prefix}-tasks"
  description = "ECS tasks: traffic from internal ALB only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "From internal ALB"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb[0].id]
  }

  egress {
    description = "DNS and customer-governed egress (VPC endpoints / hybrid path)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "efs" {
  count = local.enabled ? 1 : 0

  name        = "${local.name_prefix}-efs"
  description = "EFS NFS only from task security group"
  vpc_id      = var.vpc_id
  egress      = []
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_tasks" {
  count = local.enabled ? 1 : 0

  security_group_id            = aws_security_group.efs[0].id
  referenced_security_group_id = aws_security_group.tasks[0].id
  description                  = "NFS from ECS tasks"
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
}

resource "aws_efs_file_system" "actions" {
  count = local.enabled ? 1 : 0

  encrypted = true
  tags = {
    Name    = "${local.name_prefix}-actions"
    Purpose = local.efs_demo_label
  }
}

resource "aws_efs_mount_target" "actions" {
  count = local.enabled ? length(var.private_subnet_ids) : 0

  file_system_id  = aws_efs_file_system.actions[0].id
  subnet_id       = var.private_subnet_ids[count.index]
  security_groups = [aws_security_group.efs[0].id]
}

resource "aws_efs_access_point" "actions" {
  count = local.enabled ? 1 : 0

  file_system_id = aws_efs_file_system.actions[0].id

  posix_user {
    uid = tonumber(var.container_user)
    gid = tonumber(var.container_user)
  }

  root_directory {
    path = "/enterprise-api"
    creation_info {
      owner_uid   = tonumber(var.container_user)
      owner_gid   = tonumber(var.container_user)
      permissions = "0755"
    }
  }

  tags = {
    Name    = "${local.name_prefix}-actions-ap"
    Purpose = local.efs_demo_label
  }
}

resource "aws_lb" "api" {
  count = local.enabled ? 1 : 0

  name               = "${local.name_prefix}-api"
  load_balancer_type = "application"
  internal           = true
  security_groups    = [aws_security_group.alb[0].id]
  subnets            = var.private_subnet_ids
}

resource "aws_lb_target_group" "api" {
  count = local.enabled ? 1 : 0

  name        = "${local.name_prefix}-api"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/readyz"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }
}

resource "aws_lb_listener" "https" {
  count = local.enabled ? 1 : 0

  load_balancer_arn = aws_lb.api[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}

resource "aws_ecs_cluster" "this" {
  count = local.enabled ? 1 : 0

  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = var.enable_container_insights ? "enabled" : "disabled"
  }
}

resource "aws_ecs_task_definition" "api" {
  count = local.enabled ? 1 : 0

  family                   = "${local.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution[0].arn
  task_role_arn            = aws_iam_role.task[0].arn

  volume {
    name = "actions"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.actions[0].id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.actions[0].id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "enterprise-api"
      image     = var.container_image
      essential = true
      user      = var.container_user
      cpu       = tonumber(var.task_cpu)
      memory    = tonumber(var.task_memory)
      portMappings = [
        {
          containerPort = 8080
          hostPort      = 8080
          protocol      = "tcp"
        }
      ]
      environment = local.container_environment
      secrets     = local.container_secrets
      mountPoints = [
        {
          sourceVolume  = "actions"
          containerPath = "/var/lib/enterprise-api"
          readOnly      = false
        }
      ]
      readonlyRootFilesystem = true
      linuxParameters = {
        tmpfs = [
          {
            containerPath = "/tmp"
            size          = 64
            mountOptions  = ["rw", "noexec", "nosuid", "nodev"]
          }
        ]
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api[0].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "enterprise-api"
        }
      }
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz')\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  count = local.enabled ? 1 : 0

  name            = "${local.name_prefix}-api"
  cluster         = aws_ecs_cluster.this[0].id
  task_definition = aws_ecs_task_definition.api[0].arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # enable_circuit_breaker with rollback on failed deployments
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks[0].id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api[0].arn
    container_name   = "enterprise-api"
    container_port   = 8080
  }

  depends_on = [
    aws_lb_listener.https,
    aws_efs_mount_target.actions,
  ]
}
