# dev root config — instantiates the reusable agent_gateway module.
# Runs the gateway as API Gateway HTTP API -> Lambda, with the SAME code the
# local `make run-gateway` serves.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "serverless-agentic-opa-guardrails"
      Environment = "dev"
    }
  }
}

module "agent_gateway" {
  source = "../../modules/agent_gateway"

  agent_name        = var.agent_name
  environment       = "dev"
  region            = var.aws_region
  lambda_source_dir = var.lambda_source_dir

  # FinOps
  memory_size          = var.memory_size
  reserved_concurrency = var.reserved_concurrency
  log_retention_days   = var.log_retention_days

  # Policy version (git-derived at deploy time; falls back to "dev")
  policy_version = var.policy_version

  # Security / Compliance
  agent_role_arn        = var.agent_role_arn
  kms_key_arn           = var.kms_key_arn
  compliance_frameworks = var.compliance_frameworks
  allowed_origins       = var.allowed_origins
  tags                  = var.tags
}
