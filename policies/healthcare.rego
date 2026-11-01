# ai.guardrails.healthcare — healthcare / healthtech demo surface
#
# Demonstrates the gate for a healthcare agent (REQUIREMENTS.md §1.1):
#   - B (HERO): content-based gating of PHI in tool args (a PHI query may
#       only request a permitted scope, and only if the principal is
#       authorized for it).
#   - G (HERO): output redaction — patient identifiers (MRN, SSN, name, DOB)
#       are stripped from tool output before it reaches the LLM.
#   - A/C/H baseline: default-deny, verb-classes, context/state.
#
# The nouns differ; the gate shape is identical to fintech/secops.
package ai.guardrails.healthcare

import future.keywords.in
import rego.v1

import data.ai.guardrails.base as g

# ---------------------------------------------------------------------------
# H — default-deny (not overridden).
# ---------------------------------------------------------------------------
default allow := false
default reason := "BLOCKED: default-deny — no rule permits this tool invocation."

# Aliases on input (self-contained; do not rely on base bindings).
tool       := input.tool
args       := input.args
principal  := input.principal
ctx        := input.context

# Tool verb-classes (A). Nouns are healthcare-specific.
read_tools        := {"query_phi", "read_patient_record", "get_lab_results", "list_appointments"}
mutate_tools      := {"update_patient_demographics", "submit_claim", "schedule_appointment"}
destructive_tools := {"delete_patient_record", "anonymize_patient_record", "bulk_export_records"}

# Sensitive output fields (G): PHI identifiers stripped from tool results.
sensitive_fields := ["patient_mrn", "ssn", "patient_name", "dob", "email"]

# Permitted PHI query scopes (B). `minimum_necessary` (HIPAA) — never a
# catch-all.
phi_scopes := {"diagnosis", "medication", "encounter"}

# ---------------------------------------------------------------------------
# B — argument/payload inspection (HERO): PHI content-gating.
#   query_phi: args.scope must be a permitted PHI scope AND the principal
#   must be authorized for it (roles include "clinician" for clinical scopes).
#   A scope of "all"/"*" is always denied.
# ---------------------------------------------------------------------------
query_phi_scope_allowed if {
    args.scope in phi_scopes
    principal.authorized_scopes == args.scope
}

# ---------------------------------------------------------------------------
# ALLOW rules.
# ---------------------------------------------------------------------------

# A — read tools (except PHI, which additionally requires B).
allow if {
    tool in read_tools
    principal.authenticated == true
    tool != "query_phi"
}
allow if {
    tool == "query_phi"
    principal.authenticated == true
    query_phi_scope_allowed
}

# A — mutate tools: authenticated + safe (not leased) target.
allow if {
    tool in mutate_tools
    principal.authenticated == true
    not ctx.target.leased
}

# B + F + C + D + E — destructive tools: change-ticket + context + trajectory
#      + rate + not a preview.
allow if {
    tool in destructive_tools
    principal.authenticated == true
    g.approval_ok(ctx)
    g.context_ok(ctx)
    g.trajectory_ok(ctx, destructive_tools)
    g.rate_ok(ctx, destructive_tools)
    object.get(args, "dry_run", false) == false
}

# ---------------------------------------------------------------------------
# Distinct DENY reasons.
# ---------------------------------------------------------------------------
reason = "BLOCKED: principal is not authenticated." if {
    not principal.authenticated
}
reason = "BLOCKED: PHI query scope is not permitted or not authorized (feature B)." if {
    tool == "query_phi"
    principal.authenticated == true
    not query_phi_scope_allowed
}
reason = "BLOCKED: tool is not in a known healthcare verb-class." if {
    not tool in read_tools
    not tool in mutate_tools
    not tool in destructive_tools
}
reason = "BLOCKED: mutate target is leased elsewhere." if {
    tool in mutate_tools
    principal.authenticated == true
    ctx.target.leased
}
reason = "BLOCKED: destructive action requires an approved change ticket (feature F)." if {
    tool in destructive_tools
    principal.authenticated == true
    not g.approval_ok(ctx)
}
reason = "BLOCKED: destructive action requires an isolated, unleased target (feature C)." if {
    tool in destructive_tools
    principal.authenticated == true
    g.approval_ok(ctx)
    not g.context_ok(ctx)
}
reason = "BLOCKED: destructive action requires a describe + dry_run in the session trajectory (feature D)." if {
    tool in destructive_tools
    principal.authenticated == true
    g.approval_ok(ctx)
    g.context_ok(ctx)
    not g.trajectory_ok(ctx, destructive_tools)
}
reason = "BLOCKED: rate limit exceeded — too many destructive ops in the window (feature E)." if {
    tool in destructive_tools
    principal.authenticated == true
    g.approval_ok(ctx)
    g.context_ok(ctx)
    g.trajectory_ok(ctx, destructive_tools)
    not g.rate_ok(ctx, destructive_tools)
}
reason = "Action approved by policy engine." if { allow }

# ---------------------------------------------------------------------------
# G — output redaction (HERO): strip sensitive PHI fields from tool results.
# ---------------------------------------------------------------------------
redact = sensitive_fields if {
    tool in {"query_phi", "read_patient_record", "get_lab_results"}
}