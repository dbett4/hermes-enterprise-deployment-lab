data "aws_iam_policy_document" "ecs_assume" {
  count = var.enable_deployment ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  count = var.enable_deployment ? 1 : 0

  name               = "${var.name_prefix}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume[0].json
}

resource "aws_iam_role" "task" {
  count = var.enable_deployment ? 1 : 0

  name               = "${var.name_prefix}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume[0].json
}

data "aws_iam_policy_document" "execution_logs" {
  count = var.enable_deployment ? 1 : 0

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.api[0].arn}:*",
    ]
  }
}

data "aws_iam_policy_document" "execution_artifacts" {
  count = var.enable_deployment ? 1 : 0

  statement {
    sid    = "ReadProvidedSecretsOnly"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      var.read_secret_arn,
      var.write_secret_arn,
    ]
  }

  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ReadProvidedImageOnly"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = [var.ecr_repository_arn]
  }
}

resource "aws_iam_role_policy" "execution_logs" {
  count = var.enable_deployment ? 1 : 0

  name   = "${var.name_prefix}-execution-logs"
  role   = aws_iam_role.execution[0].id
  policy = data.aws_iam_policy_document.execution_logs[0].json
}

resource "aws_iam_role_policy" "execution_artifacts" {
  count = var.enable_deployment ? 1 : 0

  name   = "${var.name_prefix}-execution-artifacts"
  role   = aws_iam_role.execution[0].id
  policy = data.aws_iam_policy_document.execution_artifacts[0].json
}

data "aws_iam_policy_document" "task_efs" {
  count = var.enable_deployment ? 1 : 0

  statement {
    sid    = "MountAndWriteThroughProvidedAccessPoint"
    effect = "Allow"
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
    ]
    resources = [aws_efs_file_system.actions[0].arn]

    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.actions[0].arn]
    }
  }
}

resource "aws_iam_role_policy" "task_efs" {
  count = var.enable_deployment ? 1 : 0

  name   = "${var.name_prefix}-task-efs"
  role   = aws_iam_role.task[0].id
  policy = data.aws_iam_policy_document.task_efs[0].json
}

# The task role has only EFS ClientMount/ClientWrite on the created filesystem,
# constrained to the created access point. It receives no ClientRootAccess.
# The execution role may authenticate to ECR (that API requires Resource="*")
# but image-layer reads are scoped to ecr_repository_arn. Application
# credentials are scoped to the two supplied Secrets Manager ARNs.
