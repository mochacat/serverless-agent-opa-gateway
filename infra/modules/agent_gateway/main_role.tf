# ---------------------------------------------------------------------------
# SECURITY: least-privilege execution role.
# The Lambda writes audit records to CloudWatch Logs and nothing else. It gets
# no wildcard actions and no access to any resource beyond its own log group;
# KMS grants are scoped to the one CMK and only when one is configured.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name_prefix}-gw-lambda"
  description        = "Execution role for the ${local.name_prefix} agent gateway Lambda (least-privilege)."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "lambda_logs" {
  # Log writes are scoped to this function's log group only — no logs:* wildcard.
  statement {
    sid    = "WriteOwnAuditLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  # Decrypt/encrypt with the configured CMK only (skipped when using the
  # AWS-managed key).
  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [var.kms_key_arn]
    content {
      sid       = "UseConfiguredCmkForLogs"
      effect    = "Allow"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey*"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "lambda_logs" {
  name   = "${local.name_prefix}-gw-lambda-logs"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_logs.json
}
