# ai.guardrails.healthcare_test — healthcare demo surface tests.
# Hero cases (B, G) outnumber baseline (A, C, H).
package ai.guardrails.healthcare

import data.ai.guardrails.healthcare as f

# --- B (HERO): PHI content-gating ---------------------------------------------

test_phi_allowed_with_authorized_scope if {
    f.allow with input as {
        "tool": "query_phi",
        "args": {"scope": "diagnosis"},
        "principal": {"id": "dr-smith", "authenticated": true, "authorized_scopes": "diagnosis"},
        "context": {},
    }
}

test_phi_denied_catchall_scope if {
    not f.allow with input as {
        "tool": "query_phi",
        "args": {"scope": "all"},
        "principal": {"id": "dr-smith", "authenticated": true, "authorized_scopes": "all"},
        "context": {},
    }
}

test_phi_denied_unauthorized_scope if {
    not f.allow with input as {
        "tool": "query_phi",
        "args": {"scope": "diagnosis"},
        "principal": {"id": "billing", "authenticated": true, "authorized_scopes": "encounter"},
        "context": {},
    }
}

test_phi_denied_missing_scope if {
    not f.allow with input as {
        "tool": "query_phi",
        "args": {},
        "principal": {"id": "dr-smith", "authenticated": true, "authorized_scopes": "diagnosis"},
        "context": {},
    }
}

test_phi_reason_mentions_feature_b if {
    f.reason == "BLOCKED: PHI query scope is not permitted or not authorized (feature B)." with input as {
        "tool": "query_phi",
        "args": {"scope": "all"},
        "principal": {"id": "dr-smith", "authenticated": true, "authorized_scopes": "all"},
        "context": {},
    }
}

# --- G (HERO): output redaction -----------------------------------------------

test_redact_applies_to_query_phi if {
    f.redact == ["patient_mrn", "ssn", "patient_name", "dob", "email"] with input as {
        "tool": "query_phi", "args": {}, "principal": {}, "context": {},
    }
}

test_redact_applies_to_read_patient_record if {
    f.redact == ["patient_mrn", "ssn", "patient_name", "dob", "email"] with input as {
        "tool": "read_patient_record", "args": {}, "principal": {}, "context": {},
    }
}

test_no_redact_for_mutate if {
    not f.redact with input as {
        "tool": "update_patient_demographics", "args": {}, "principal": {}, "context": {},
    }
}

# --- F (destructive approval) --------------------------------------------------

test_delete_denied_without_ticket if {
    not f.allow with input as {
        "tool": "delete_patient_record",
        "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {},
    }
}

test_delete_allowed_with_full_conditions if {
    f.allow with input as {
        "tool": "delete_patient_record",
        "args": {"dry_run": false},
        "principal": {"id": "svc", "authenticated": true},
        "context": {
            "approval": {"reference": "CHG-77", "approved": true},
            "target": {"isolated": true, "leased": false},
            "rate_window": {"mutations": 0, "max": 3},
            "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
        },
    }
}

# --- A / H (baseline) -----------------------------------------------------------

test_read_allowed_authenticated if {
    f.allow with input as {
        "tool": "get_lab_results", "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"leased": false}},
    }
}

test_read_denied_unauthenticated if {
    not f.allow with input as {
        "tool": "get_lab_results", "args": {},
        "principal": {"id": "anon", "authenticated": false},
        "context": {},
    }
}

test_default_deny_unknown_tool if {
    not f.allow with input as {
        "tool": "rm_rf", "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {},
    }
}

test_mutate_denied_leased_target if {
    not f.allow with input as {
        "tool": "submit_claim", "args": {},
        "principal": {"id": "svc", "authenticated": true},
        "context": {"target": {"leased": true}},
    }
}