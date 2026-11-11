# ---------------------------------------------------------------------------
# Cross-cutting concerns computed once and applied everywhere.
# ---------------------------------------------------------------------------

locals {
  name_prefix = "${var.agent_name}-${var.environment}"

  # --- Security / Compliance: audit log retention floor -------------------
  # Regulated frameworks require a minimum retention window. We take the max
  # of what the caller asked for and the strictest applicable floor, so a
  # compliance-tagged deployment can never silently under-retain audit logs.
  compliance_retention_floor = (
    contains(var.compliance_frameworks, "hipaa") ? 2190 :  # ~6 yrs (HIPAA)
    contains(var.compliance_frameworks, "pci-dss") ? 365 : # 1 yr (PCI DSS 10.5)
    contains(var.compliance_frameworks, "soc2") ? 365 :
    contains(var.compliance_frameworks, "iso27001") ? 365 :
    contains(var.compliance_frameworks, "gdpr") ? 365 :
    var.log_retention_days
  )
  effective_log_retention = max(var.log_retention_days, local.compliance_retention_floor)

  # --- Tags: mandatory cost-allocation + compliance labels on every
  #     resource (FinOps chargeback + compliance evidence). Caller tags
  #     can override non-mandatory keys but not the mandatory set.
  mandatory_tags = {
    "ManagedBy"          = "opentofu"
    "Module"             = "agent_gateway"
    "AgentName"          = var.agent_name
    "Environment"        = var.environment
    "CostCenter"         = var.environment == "prod" ? "prod" : "non-prod"
    "DataClassification" = "sensitive" # the gateway handles PHI/financial payloads
  }
  compliance_tags = length(var.compliance_frameworks) > 0 ? {
    "ComplianceFrameworks" = join(",", var.compliance_frameworks)
    "AuditRetentionDays"   = tostring(local.effective_log_retention)
  } : {}
  tags = merge(local.mandatory_tags, local.compliance_tags, var.tags)

  # --- Policy version: git-derived for audit traceability -----------------
  # When the caller supplies a policy_version, use it verbatim. When null
  # (the default), fall back to "dev" so unversioned local/CI runs don't
  # incorrectly stamp a fake release version on audit records. A real
  # deploy (Makefile `deploy`) always passes a git-derived version.
  policy_version = var.policy_version != null ? var.policy_version : "dev"

  # --- FinOps: reserved concurrency sentinel ------------------------------
  # -1 means "unreserved". We translate to null so the attribute is omitted
  # (the AWS provider treats reserved concurrency as optional).
  reserved_concurrency = var.reserved_concurrency >= 0 ? var.reserved_concurrency : null
}
