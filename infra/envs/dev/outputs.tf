output "gateway_url" {
  description = "Base URL of the deployed gateway."
  value       = module.agent_gateway.gateway_url
}

output "tools_call_url" {
  description = "POST agents send tool dispatches to."
  value       = module.agent_gateway.tools_call_url
}

output "lambda_arn" {
  value = module.agent_gateway.lambda_arn
}

output "audit_log_group" {
  description = "CloudWatch log group holding the audit trail."
  value       = module.agent_gateway.audit_log_group
}

output "effective_log_retention_days" {
  description = "Retention actually applied after the compliance floor."
  value       = module.agent_gateway.effective_log_retention_days
}

output "tags" {
  description = "Mandatory + compliance tags applied everywhere."
  value       = module.agent_gateway.tags
}
