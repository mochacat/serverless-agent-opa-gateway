# ai.guardrails.base — shared, pure helpers for the serverless agent gateway
#
# This package contains ONLY pure, argument-taking helpers shared by every
# industry package. It holds no `allow`/`reason`/`redact` and no variable
# bindings on `input` — each industry package is self-contained (it defines
# its own verb-classes, aliases, and rules) and calls these helpers with
# explicit arguments.
#
# The gateway gates TOOL DISPATCH, not the LLM request. The model call is
# never proxied (DESIGN §3.3a). Cross-call state (trajectory D, rate E) is
# injected via input.context from a swappable SessionStore; OPA stays
# pure/stateless.
#
# Capability surface (DESIGN §4.3):
#   H default-deny (baseline) · A verb-classes (baseline) · C context/state (baseline)
#   B argument/payload inspection · E rate/deny-window · F change-ticket · G output redact
#   D trajectory   (heroes: B, E, F, G)
package ai.guardrails.base

import future.keywords.in

# ---------------------------------------------------------------------------
# F — change-ticket approved? An approval object with a non-empty reference
#      that is `approved: true`. (Separation-of-duties: destructive/money
#      actions need a human change ticket, not self-approval.)
# ---------------------------------------------------------------------------
approval_ok(ctx) if {
    object.get(ctx, "approval", {}) != {}
    ctx.approval.reference != ""
    ctx.approval.approved == true
}

# C — context/state: target must be isolated and not leased elsewhere
#      (supplied by SessionStore / infra). Conservative default: false.
# ---------------------------------------------------------------------------
context_ok(ctx) if {
    object.get(ctx.target, "isolated", false) == true
    ctx.target.leased == false
}

# D — trajectory: a `describe`/`dry_run` must have preceded a destructive
#      action in the session trajectory (look-before-you-leap).
#      `destructive` is the industry's set of destructive tools.
# ---------------------------------------------------------------------------
trajectory_ok(ctx, destructive) if {
    some entry in object.get(ctx, "trajectory", [])
    entry.tool == "describe_cluster"
    some entry2 in object.get(ctx, "trajectory", [])
    entry2.tool == "dry_run"
}

# E — rate/deny-window: count of destructive ops in the trajectory window
#      must not exceed the configured max. Conservative default: 0.
#      `destructive` is the industry's set of destructive tools.
#
#      NOTE: this counts distinct destructive CALLS (array comprehension,
#      which preserves duplicates), not distinct tool TYPES (a set
#      comprehension would collapse two identical tool names to one).
# ---------------------------------------------------------------------------
count_destructive(ctx, destructive) := count([
    t | some t in object.get(ctx, "trajectory", []); t.tool in destructive
])

rate_ok(ctx, destructive) if {
    count_destructive(ctx, destructive) <= object.get(ctx.rate_window, "max", 0)
}

# B — regex guard for resource ids (DNS-style, no "." / ".." / wildcards).
# ---------------------------------------------------------------------------
resource_id_valid(argid) if {
    regex.match("^[a-z0-9][a-z0-9-]{2,63}$", argid)
}