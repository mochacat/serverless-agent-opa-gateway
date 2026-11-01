# ai.guardrails.base_test — tests for the shared helper package.
package ai.guardrails.base

import data.ai.guardrails.base as g

destructive := {"delete_cluster", "drop_database_table"}

# --- F: approval_ok ---------------------------------------------------------

test_approval_ok_with_approved_reference if {
    g.approval_ok({"approval": {"reference": "CHG-123", "approved": true}})
}

test_approval_denied_when_not_approved if {
    not g.approval_ok({"approval": {"reference": "CHG-123", "approved": false}})
}

test_approval_denied_when_empty_reference if {
    not g.approval_ok({"approval": {"reference": "", "approved": true}})
}

test_approval_denied_when_no_approval if {
    not g.approval_ok({})
}

# --- C: context_ok ----------------------------------------------------------

test_context_ok_isolated_unleased if {
    g.context_ok({"target": {"isolated": true, "leased": false}})
}

test_context_denied_when_leased if {
    not g.context_ok({"target": {"isolated": true, "leased": true}})
}

test_context_denied_when_not_isolated if {
    not g.context_ok({"target": {"isolated": false, "leased": false}})
}

test_context_denied_when_missing_target if {
    not g.context_ok({})
}

# --- D: trajectory_ok --------------------------------------------------------

test_trajectory_ok_with_describe_and_dry_run if {
    g.trajectory_ok({
        "trajectory": [
            {"tool": "describe_cluster", "at": "t0"},
            {"tool": "dry_run", "at": "t1"},
        ],
    }, destructive)
}

test_trajectory_denied_without_describe if {
    not g.trajectory_ok({"trajectory": [{"tool": "dry_run", "at": "t0"}]}, destructive)
}

test_trajectory_denied_without_dry_run if {
    not g.trajectory_ok({"trajectory": [{"tool": "describe_cluster", "at": "t0"}]}, destructive)
}

test_trajectory_denied_empty if {
    not g.trajectory_ok({}, destructive)
}

# --- E: count_destructive / rate_ok -----------------------------------------

# Array comprehension counts distinct CALLS (not distinct tool types).
test_count_destructive_counts_calls_not_types if {
    count_destructive({"trajectory": [
        {"tool": "delete_cluster"}, {"tool": "delete_cluster"},
    ]}, destructive) == 2
}

test_count_destructive_zero_when_none if {
    count_destructive({"trajectory": [{"tool": "get_status"}]}, destructive) == 0
}

test_rate_ok_within_window if {
    g.rate_ok({
        "trajectory": [{"tool": "delete_cluster"}],
        "rate_window": {"mutations": 0, "max": 2},
    }, destructive)
}

test_rate_denied_over_window if {
    not g.rate_ok({
        "trajectory": [
            {"tool": "delete_cluster"}, {"tool": "delete_cluster"},
        ],
        "rate_window": {"mutations": 0, "max": 1},
    }, destructive)
}

# --- B: resource_id_valid -----------------------------------------------------

test_resource_id_valid_ok if {
    g.resource_id_valid("prod-eu-1")
}

test_resource_id_valid_rejects_dot if {
    not g.resource_id_valid("prod..eu")
}

test_resource_id_valid_rejects_wildcard if {
    not g.resource_id_valid("*")
}

test_resource_id_valid_rejects_too_short if {
    not g.resource_id_valid("ab")
}