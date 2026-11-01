# ai.guardrails_test — tests for the top-level dispatch package.
package ai.guardrails

# --- Fail-closed (unknown / empty industry) -------------------------------------

test_unknown_industry_default_deny if {
    not allow with input as {
        "industry": "gaming",
        "tool": "get_balance",
        "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {},
    }
}

test_empty_industry_default_deny if {
    not allow with input as {
        "industry": "",
        "tool": "get_balance",
        "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {},
    }
}

test_unknown_industry_reason if {
    reason == "BLOCKED: unknown or unset industry — default-deny." with input as {
        "industry": "gaming", "tool": "get_balance", "args": {},
        "principal": {"id": "svc", "authenticated": true}, "context": {},
    }
}

test_decision_defined_for_unknown_industry if {
    decision == {"allow": false, "reason": "BLOCKED: unknown or unset industry — default-deny."} with input as {
        "industry": "gaming", "tool": "get_balance", "args": {},
        "principal": {"id": "svc", "authenticated": true}, "context": {},
    }
}

# --- Routing to industry surfaces ------------------------------------------------

test_healthcare_route_denies_destructive_without_ticket if {
    not allow with input as {
        "industry": "healthcare", "tool": "delete_patient_record", "args": {},
        "principal": {"id": "svc", "authenticated": true}, "context": {},
    }
}

test_fintech_route_allows_approved_transfer if {
    allow with input as {
        "industry": "fintech", "tool": "execute_transfer",
        "args": {"amount": 100, "beneficiary": "payroll"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "approval": {"reference": "CHG-100", "approved": true},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

test_secops_route_denies_bad_target if {
    not allow with input as {
        "industry": "secops", "tool": "revoke_token", "args": {"target": "*"},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "approval": {"reference": "CHG-50", "approved": true},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

# --- Decision output contract (DESIGN §4.3) ---------------------------------------

test_decision_includes_redact_for_sensitive_tool if {
    decision == {
        "allow": true,
        "reason": "Action approved by policy engine.",
        "redact": ["patient_mrn", "ssn", "patient_name", "dob", "email"],
    } with input as {
        "industry": "healthcare", "tool": "get_lab_results", "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"leased": false}},
    }
}