# ai.guardrails.fintech — fintech / banking demo surface
#
# Demonstrates the gate for a money-movement agent (REQUIREMENTS.md §1.1):
#   - F (HERO): change-ticket approval — money movement requires a human
#       approval reference, dual-control / 4-eyes (SOX, FINRA, PCI).
#   - E (HERO): rate/deny-window — cap transfer volume in a session window.
#   - B (HERO): argument inspection — amount caps + recipient allowlist.
#   - A/C/H baseline: default-deny, verb-classes, context/state.
package ai.guardrails.fintech

import future.keywords.in
import rego.v1

import data.ai.guardrails.base as g

# ---------------------------------------------------------------------------
# H — default-deny (not overridden).
# ---------------------------------------------------------------------------
default allow := false
default reason := "BLOCKED: default-deny — no rule permits this tool invocation."

# Aliases on input (self-contained).
tool       := input.tool
args       := input.args
principal  := input.principal
ctx        := input.context

read_tools        := {"get_balance", "get_account", "list_transactions", "get_rate"}
mutate_tools      := {"create_transfer", "update_beneficiary", "send_otp"}
destructive_tools := {"execute_transfer", "approve_payment", "revoke_card", "close_account"}

# Sensitive output fields (G).
sensitive_fields := ["account_number", "card_number", "ssn", "routing_number"]

# B — argument inspection: single-transfer amount cap + beneficiary allowlist.
max_transfer_amount := 10000
known_beneficiaries := {"payroll", "vendor_alpha", "vendor_beta", "refund_desk"}

amount      := object.get(args, "amount", 0)
beneficiary := object.get(args, "beneficiary", "")
amount_valid if {
    amount >= 0
}

# ---------------------------------------------------------------------------
# ALLOW rules.
# ---------------------------------------------------------------------------

# A — read tools: authenticated.
allow if {
    tool in read_tools
    principal.authenticated == true
}

# A — mutate tools: authenticated + safe target.
allow if {
    tool in mutate_tools
    principal.authenticated == true
    not ctx.target.leased
}

# B + F + C + D + E — destructive (money movement): amount cap + beneficiary
#      allowlist + human approval + context + trajectory + rate.
allow if {
    tool in destructive_tools
    principal.authenticated == true
    amount_valid
    amount <= max_transfer_amount
    beneficiary in known_beneficiaries
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
reason = "BLOCKED: transfer amount exceeds the single-transfer cap (feature B)." if {
    tool in destructive_tools
    principal.authenticated == true
    amount_valid
    amount > max_transfer_amount
}
reason = "BLOCKED: beneficiary is not on the approved allowlist (feature B)." if {
    tool in destructive_tools
    principal.authenticated == true
    amount_valid
    amount <= max_transfer_amount
    not beneficiary in known_beneficiaries
}
reason = "BLOCKED: money movement requires an approved change ticket (dual-control, feature F)." if {
    tool in destructive_tools
    principal.authenticated == true
    amount_valid
    amount <= max_transfer_amount
    beneficiary in known_beneficiaries
    not g.approval_ok(ctx)
}
reason = "BLOCKED: destructive action requires an isolated, unleased target (feature C)." if {
    tool in destructive_tools
    principal.authenticated == true
    amount_valid
    amount <= max_transfer_amount
    beneficiary in known_beneficiaries
    g.approval_ok(ctx)
    not g.context_ok(ctx)
}
reason = "BLOCKED: destructive action requires a describe + dry_run in the session trajectory (feature D)." if {
    tool in destructive_tools
    principal.authenticated == true
    amount_valid
    amount <= max_transfer_amount
    beneficiary in known_beneficiaries
    g.approval_ok(ctx)
    g.context_ok(ctx)
    not g.trajectory_ok(ctx, destructive_tools)
}
reason = "BLOCKED: rate limit exceeded — transfer volume in the window is too high (feature E)." if {
    tool in destructive_tools
    principal.authenticated == true
    amount_valid
    amount <= max_transfer_amount
    beneficiary in known_beneficiaries
    g.approval_ok(ctx)
    g.context_ok(ctx)
    g.trajectory_ok(ctx, destructive_tools)
    not g.rate_ok(ctx, destructive_tools)
}
reason = "Action approved by policy engine." if { allow }

# ---------------------------------------------------------------------------
# G — output redaction (HERO): strip account/card identifiers from results.
# ---------------------------------------------------------------------------
redact = sensitive_fields if {
    tool in {"get_balance", "get_account", "list_transactions", "get_rate"}
}