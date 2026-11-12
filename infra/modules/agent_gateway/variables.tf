# ---------------------------------------------------------------------------
# Inputs. Grouped by concern: core wiring, then the three cross-cutting
# requirements this module bakes in — Security, FinOps and Compliance — so a
# caller configures them deliberately instead of inheriting insecure/expensive
# defaults. Every default is the safe, cheap, auditable choice.
# ---------------------------------------------------------------------------

# ---------------------------------------------- core ----------------------
variable "agent_name" {
  description = "Short name for the agent gateway (used in resource names + tags)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.agent_name))
    error_message = "agent_name must be 2-31 chars, lowercase alphanumeric or hyphen, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment (e.g. dev, staging, prod)."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "lambda_source_dir" {
  description = "Directory whose contents are zipped into the Lambda package (pre-built: src/ + policies/ + pip deps)."
  type        = string
}

variable "lambda_handler" {
  description = "Lambda handler entrypoint (module.function)."
  type        = string
  default     = "handler.handler"
}

variable "runtime" {
  description = "Lambda Python runtime."
  type        = string
  default     = "python3.14"
}

# (agent_role_arn was removed — the tools_call route uses AWS_IAM auth
# which already restricts invocation via IAM permissions.)
variable "kms_key_arn" {
  description = <<-EOT
    Customer-managed KMS key for CloudWatch Logs + Lambda env encryption.
    STRONGLY RECOMMENDED in prod (HIPAA/PCI). Null = AWS-managed key (dev only).
  EOT
  type        = string
  default     = null
}

variable "allowed_origins" {
  description = "CORS allowed origins for the HTTP API. Default denies all cross-origin (agents call server-to-server)."
  type        = list(string)
  default     = []
}

variable "vpc_subnet_ids" {
  description = "Optional VPC subnet IDs to attach the Lambda to (private egress). Empty = no VPC."
  type        = list(string)
  default     = []
}

variable "vpc_security_group_ids" {
  description = "Security group IDs for the Lambda when a VPC is used."
  type        = list(string)
  default     = []
}

variable "api_throttling_burst_limit" {
  description = "HTTP API burst limit (requests). Caps a runaway agent client (security + FinOps)."
  type        = number
  default     = 100
}

variable "api_throttling_rate_limit" {
  description = "HTTP API steady-state rate limit (requests/sec)."
  type        = number
  default     = 50
}

# ---------------------------------------------- FINOPS --------------------
variable "memory_size" {
  description = "Lambda memory (MB). More memory also buys CPU; right-size against your policy-eval latency target."
  type        = number
  default     = 512

  validation {
    condition     = var.memory_size >= 128 && var.memory_size <= 10240
    error_message = "memory_size must be between 128 and 10240 MB."
  }
}

variable "reserved_concurrency" {
  description = <<-EOT
    Reserved concurrency cap (FinOps guardrail: bounds max parallel executions
    and therefore max cost). -1 = unreserved (NOT recommended for prod).
  EOT
  type        = number
  default     = 10
}

variable "log_retention_days" {
  description = "CloudWatch audit log retention (days). Compliance floor is enforced below."
  type        = number
  default     = 365
}

# ---------------------------------------------- COMPLIANCE ----------------
variable "compliance_frameworks" {
  description = "Frameworks this deployment supports (e.g. [\"hipaa\",\"soc2\"]). Drives tags + a retention floor."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for f in var.compliance_frameworks : contains(["hipaa", "soc2", "iso27001", "pci-dss", "gdpr"], f)])
    error_message = "compliance_frameworks entries must be among: hipaa, soc2, iso27001, pci-dss, gdpr."
  }
}

variable "enable_api_access_logs" {
  description = "Emit API Gateway access logs to CloudWatch (request-level audit trail)."
  type        = bool
  default     = true
}

variable "policy_version" {
  description = <<-EOT
    Policy version stamped into every audit record — enables traceability from
    an audit log entry back to the exact git revision of the policies that were
    loaded. Defaults to "dev" when null (local/CI) so the local workflow never
    incorrectly stamps "v1" on records from unreleased changes.
  EOT
  type        = string
  default     = null
}

variable "tags" {
  description = "Additional tags merged over the module's mandatory tags (cost allocation + compliance)."
  type        = map(string)
  default     = {}
}
