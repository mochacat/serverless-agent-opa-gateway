# Compliance

This document describes how the gateway **supports** compliance in regulated
environments. It is not a certification — the gateway is a tool, and compliance
is an organizational practice.

## What the gateway provides

| Control | How the gateway supports it |
|---------|----------------------------|
| **Audit trail** (HIPAA §164.312(b), PCI DSS 10.2, SOC2 CC6.1) | Every allow AND deny is recorded with tool, args, principal, decision, reason, trace_id, and timestamp. Records are immutable once written. |
| **Least-privilege access** (HIPAA §164.312(a), PCI DSS 7.1, SOC2 CC6.3) | The agent holds no credentials — the gateway resolves identity server-side from IAM. Rego policies enforce verb-class gating (A), scope authorization (B), and change-ticket requirements (F). |
| **Separation of duties** (SOX, PCI DSS 8.5, SOC2 CC6.2) | Destructive actions require a human-approved change ticket (F). The agent cannot self-approve — the approval reference is injected server-side. |
| **Data minimization** (HIPAA §164.514(b), GDPR Art. 5(1)(c)) | Output redaction (G) strips PHI/PII from tool results before the model sees them. The agent never accesses raw sensitive data. |
| **Retention** (HIPAA §164.316(b)(2), PCI DSS 10.7, SOC2 CC6.4) | Audit log retention is configurable per environment and enforced by a compliance floor (6 years for HIPAA, 1 year for PCI/SOC2/ISO/GDPR). |
| **Encryption at rest** (HIPAA §164.312(a)(2)(iv), PCI DSS 4.1) | Lambda log groups and environment variables can be encrypted with a customer-managed KMS key. |

## Scope boundaries

The gateway does **not** cover:

- **LLM04** (Model Denial of Service) — the gateway bounds tool dispatch, not
  model invocation. Rate limits on the model API are a separate concern.
- **LLM05** (Supply Chain) — the gateway does not scan model artifacts,
  dependencies, or training data.
- **LLM07** (Sensitive Info in Training Data) — data used to train or fine-tune
  models is out of scope.
- **LLM08** (Excessive Agency via Plugin/Tool Chaining) — the gateway gates
  individual tool dispatches; it does not analyze multi-step attack chains
  across sessions.
- **LLM09** (Over-reliance) — the gateway does not verify the accuracy or
  appropriateness of model outputs beyond redacting sensitive fields.

## Configuring compliance frameworks

Set `compliance_frameworks` in your OpenTofu config:

```hcl
compliance_frameworks = ["hipaa", "soc2"]
```

This automatically:
- Raises audit log retention to the strictest floor (6 years for HIPAA)
- Tags all resources with `ComplianceFrameworks` and `AuditRetentionDays`
- Sets `DataClassification: sensitive` on all resources

See `infra/modules/agent_gateway/locals.tf` for the retention floor logic.