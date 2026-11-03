# Environment-level inputs for the dev root config. Values come from
# `terraform.tfvars` (copy terraform.tfvars.example) or `-var` flags.

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "agent_name" {
  description = "Agent gateway name (namespacing + tags)."
  type        = string
  default     = "agent-gw"
}

variable "lambda_source_dir" {
  description = "Directory of the built Lambda package root (contains src/ + policies/). Built by `make build-zip`."
  type        = string
  default     = "../../../build/lambda"
}

# -------- FinOps knobs (dev defaults are deliberately cheap/small) ---------
variable "memory_size" {
  description = "Lambda memory (MB)."
  type        = number
  default     = 512
}

variable "reserved_concurrency" {
  description = "Reserved concurrency cap (-1 = unreserved)."
  type        = number
  default     = 2
}

variable "log_retention_days" {
  description = "Requested audit log retention (compliance floor may raise it)."
  type        = number
  default     = 30
}

# -------- Security / Compliance knobs --------------------------------------
variable "kms_key_arn" {
  description = "Optional CMK ARN for log/Lambda encryption."
  type        = string
  default     = null
}

variable "compliance_frameworks" {
  description = "Frameworks this env supports (drives tags + retention floor)."
  type        = list(string)
  default     = []
}

variable "allowed_origins" {
  description = "CORS origins (empty = deny all cross-origin)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags merged over the module's mandatory tags."
  type        = map(string)
  default     = {}
}
