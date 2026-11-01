# ai.guardrails.fintech_test — fintech demo surface tests.
# Hero cases (B, F, E) outnumber baseline (A, C, H).
package ai.guardrails.fintech

import data.ai.guardrails.fintech as f

# A fully-approved money-movement context (used by the allowed case).
approved_ctx := {
    "target": {"isolated": true, "leased": false},
    "approval": {"reference": "CHG-100", "approved": true},
    "rate_window": {"mutations": 0, "max": 3},
    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
}

# --- B (HERO): amount cap ------------------------------------------------------

test_transfer_denied_over_amount_cap if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 50000, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": approved_ctx,
    }
}

test_transfer_allowed_at_cap if {
    f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 10000, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": approved_ctx,
    }
}

test_transfer_denied_negative_amount if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": -5, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": approved_ctx,
    }
}

test_amount_over_cap_reason if {
    f.reason == "BLOCKED: transfer amount exceeds the single-transfer cap (feature B)." with input as {
        "tool": "execute_transfer",
        "args": {"amount": 50000, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": approved_ctx,
    }
}

# --- B (HERO): beneficiary allowlist -------------------------------------------

test_transfer_denied_unknown_beneficiary if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "unknown_llc"},
        "principal": {"id": "svc", "authenticated": true},
        "context": approved_ctx,
    }
}

test_transfer_denied_missing_beneficiary if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100},
        "principal": {"id": "svc", "authenticated": true},
        "context": approved_ctx,
    }
}

test_unknown_beneficiary_reason if {
    f.reason == "BLOCKED: beneficiary is not on the approved allowlist (feature B)." with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "unknown_llc"},
        "principal": {"id": "svc", "authenticated": true},
        "context": approved_ctx,
    }
}

# --- F (HERO): dual-control / change-ticket -----------------------------------

test_transfer_denied_without_ticket if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

test_transfer_denied_unapproved_ticket if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "approval": {"reference": "CHG-100", "approved": false},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

test_no_ticket_reason if {
    f.reason == "BLOCKED: money movement requires an approved change ticket (dual-control, feature F)." with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

# --- E (HERO): rate/deny-window ------------------------------------------------

test_transfer_denied_over_rate_window if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "approval": {"reference": "CHG-100", "approved": true},
                    "rate_window": {"mutations": 0, "max": 1},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"},
                                   {"tool": "execute_transfer"}, {"tool": "execute_transfer"}]},
    }
}

test_rate_reason if {
    f.reason == "BLOCKED: rate limit exceeded — transfer volume in the window is too high (feature E)." with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "approval": {"reference": "CHG-100", "approved": true},
                    "rate_window": {"mutations": 0, "max": 1},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"},
                                   {"tool": "execute_transfer"}, {"tool": "execute_transfer"}]},
    }
}

# --- D (trajectory) -------------------------------------------------------------

test_transfer_denied_without_trajectory if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "approval": {"reference": "CHG-100", "approved": true},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "get_balance"}]},
    }
}

# --- A / C / H (baseline) --------------------------------------------------------

test_read_allowed_authenticated if {
    f.allow with input as {
        "tool": "get_balance", "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {},
    }
}

test_default_deny_unknown_tool if {
    not f.allow with input as {
        "tool": "send_money", "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {},
    }
}

test_mutate_denied_leased_target if {
    not f.allow with input as {
        "tool": "create_transfer", "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"leased": true}},
    }
}

test_transfer_denied_not_isolated if {
    not f.allow with input as {
        "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": false, "leased": false},
                    "approval": {"reference": "CHG-100", "approved": true},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

# --- G (output redaction) ---------------------------------------------------------

test_redact_applies_to_balance if {
    f.redact == ["account_number", "card_number", "ssn", "routing_number"] with input as {
        "tool": "get_balance", "args": {}, "principal": {}, "context": {},
    }
}

test_no_redact_for_transfer if {
    not f.redact with input as {
        "tool": "execute_transfer", "args": {}, "principal": {}, "context": {},
    }
}