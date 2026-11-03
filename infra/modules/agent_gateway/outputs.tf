# ---------------------------------------------------------------------------
# Outputs per DESIGN §4.7: gateway_url, lambda_arn, audit_log_group — plus a
# few the caller needs to wire agents / observability / compliance evidence.
# ---------------------------------------------------------------------------

output "gateway_url" {
  description = "Base HTTPS URL of the gateway (append /tools/call)."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "tools_call_url" {
  description = "Full URL agents POST tool dispatches to."
  value       = "${aws_apigatewayv2_stage.default.invoke_url}/tools/call"
}

output "lambda_arn" {
  description = "ARN of the gateway Lambda function."
  value       = aws_lambda_function.gateway.arn
}

output "lambda_role_arn" {
  description = "ARN of the least-privilege Lambda execution role."
  value       = aws_iam_role.lambda.arn
}

output "audit_log_group" {
  description = "CloudWatch log group holding the audit trail (N5)."
  value       = aws_cloudwatch_log_group.lambda.name
}

output "audit_log_group_arn" {
  description = "ARN of the audit log group (for log-based metric filters / alerts)."
  value       = aws_cloudwatch_log_group.lambda.arn
}

output "effective_log_retention_days" {
  description = "Actual retention applied after the compliance floor (evidence for auditors)."
  value       = local.effective_log_retention
}

output "tags" {
  description = "Mandatory + compliance tags applied to every resource."
  value       = local.tags
}
