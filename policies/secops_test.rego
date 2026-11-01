# ai.guardrails.secops_test — secops demo surface tests.
# Hero cases (B) outnumber baseline (A, C, H).
package ai.guardrails.secops

import data.ai.guardrails.secops as f

approved_ctx := {
    "target": {"isolated": true, "leased": false},
    "approval": {"reference": "CHG-50", "approved": true},
    "rate_window": {"mutations": 0, "max": 3},
    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
}

# --- B (HERO): target hostname/IP regex -----------------------------------------

test_target_valid_hostname if {
    f.allow with input as {
        "tool": "revoke_token",
        "args": {"target": "prod-web-01"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": approved_ctx,
    }
}

test_target_valid_ip if {
    f.allow with input as {
        "tool": "revoke_token",
        "args": {"target": "10.0.0.5"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": approved_ctx,
    }
}

test_target_denied_wildcard if {
    not f.allow with input as {
        "tool": "revoke_token",
        "args": {"target": "*"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": approved_ctx,
    }
}

test_target_denied_dot_traversal if {
    not f.allow with input as {
        "tool": "block_ip",
        "args": {"target": ".."},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": approved_ctx,
    }
}

test_target_denied_shell_metachar if {
    not f.allow with input as {
        "tool": "kill_process",
        "args": {"target": "host; rm -rf /"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": approved_ctx,
    }
}

test_target_denied_missing if {
    not f.allow with input as {
        "tool": "revoke_token",
        "args": {},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": approved_ctx,
    }
}

test_bad_target_reason if {
    f.reason == "BLOCKED: target is not a well-formed hostname/IP (feature B)." with input as {
        "tool": "revoke_token",
        "args": {"target": "*"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": approved_ctx,
    }
}

# --- C (context / isolation) ------------------------------------------------------

test_destructive_denied_not_isolated if {
    not f.allow with input as {
        "tool": "revoke_token",
        "args": {"target": "10.0.0.5"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": {"target": {"isolated": false, "leased": false},
                    "approval": {"reference": "CHG-50", "approved": true},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

test_destructive_denied_leased if {
    not f.allow with input as {
        "tool": "revoke_token",
        "args": {"target": "10.0.0.5"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": true},
                    "approval": {"reference": "CHG-50", "approved": true},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

test_no_context_reason if {
    f.reason == "BLOCKED: destructive action requires an isolated, unleased target (feature C)." with input as {
        "tool": "revoke_token",
        "args": {"target": "10.0.0.5"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": {"target": {"isolated": false, "leased": false}},
    }
}

# --- F (ticket) --------------------------------------------------------------------

test_destructive_denied_without_ticket if {
    not f.allow with input as {
        "tool": "revoke_token",
        "args": {"target": "10.0.0.5"},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": {"target": {"isolated": true, "leased": false},
                    "rate_window": {"mutations": 0, "max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]},
    }
}

# --- A / H (baseline) ----------------------------------------------------------------

test_read_allowed_authenticated if {
    f.allow with input as {
        "tool": "list_hosts", "args": {},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": {},
    }
}

test_default_deny_unknown_tool if {
    not f.allow with input as {
        "tool": "rm_rf", "args": {},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": {},
    }
}

test_mutate_denied_leased if {
    not f.allow with input as {
        "tool": "quarantine_host", "args": {},
        "principal": {"id": "soc-analyst", "authenticated": true},
        "context": {"target": {"leased": true}},
    }
}

# --- G (output redaction) -------------------------------------------------------------

test_redact_applies_to_list_hosts if {
    f.redact == ["internal_ip", "hostname", "token", "credential"] with input as {
        "tool": "list_hosts", "args": {}, "principal": {}, "context": {},
    }
}

test_no_redact_for_quarantine if {
    not f.redact with input as {
        "tool": "quarantine_host", "args": {}, "principal": {}, "context": {},
    }
}