# ---------------------------------------------------------------------------
# COMPLIANCE + FINOPS: audit log groups with a compliance-enforced retention.
# The Lambda log group is the durable audit trail (N5: every allow AND deny).
# Retention is max(requested, compliance floor) so audit evidence is never
# silently discarded too early (see locals.tf).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}-gw"
  retention_in_days = local.effective_log_retention
  kms_key_id        = var.kms_key_arn
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "api_access" {
  count = var.enable_api_access_logs ? 1 : 0

  name              = "/aws/apigw/${local.name_prefix}-gw/access"
  retention_in_days = local.effective_log_retention
  kms_key_id        = var.kms_key_arn
  tags              = local.tags
}
