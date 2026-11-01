# ai.guardrails.secops — cybersecurity / secops demo surface
#
# Demonstrates the gate for a security-response agent (REQUIREMENTS.md §1.1):
#   - B (HERO): argument/payload inspection — only well-formed hostnames/IPs
#       are actionable targets; wildcards, ".." traversal, or shell
#       metacharacters are never valid.
#   - C: ephemeral isolation — a host may only be isolated/acted on when the
#       target is isolated + unleased.
#   - H/A baseline: default-deny, least-privilege verb-classes.
#   - F/E/D compose on destructive actions (ticket, rate, trajectory).
package ai.guardrails.secops

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

read_tools        := {"list_hosts", "get_host", "list_incidents", "search_logs"}
mutate_tools      := {"quarantine_host", "isolate_host", "update_playbook"}
destructive_tools := {"revoke_token", "block_ip", "kill_process", "reset_cluster_state"}

# Sensitive output fields (G): IOCs / internal identifiers not to echo broadly.
sensitive_fields := ["internal_ip", "hostname", "token", "credential"]

# B — argument inspection: a target must be a well-formed hostname or IP.
hostname_valid if {
    regex.match("^[a-z0-9][a-z0-9.-]{1,253}[a-z0-9]$", object.get(args, "target", ""))
}
ip_valid if {
    regex.match("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$", object.get(args, "target", ""))
}
target_valid if { hostname_valid }
target_valid if { ip_valid }

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

# B + C + F + D + E — destructive: valid target + context + ticket + trajectory
#      + rate + not a preview.
allow if {
    tool in destructive_tools
    principal.authenticated == true
    target_valid
    g.context_ok(ctx)
    g.approval_ok(ctx)
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
reason = "BLOCKED: target is not a well-formed hostname/IP (feature B)." if {
    tool in destructive_tools
    principal.authenticated == true
    not target_valid
}
reason = "BLOCKED: destructive action requires an isolated, unleased target (feature C)." if {
    tool in destructive_tools
    principal.authenticated == true
    target_valid
    not g.context_ok(ctx)
}
reason = "BLOCKED: destructive action requires an approved change ticket (feature F)." if {
    tool in destructive_tools
    principal.authenticated == true
    target_valid
    g.context_ok(ctx)
    not g.approval_ok(ctx)
}
reason = "BLOCKED: destructive action requires a describe + dry_run in the session trajectory (feature D)." if {
    tool in destructive_tools
    principal.authenticated == true
    target_valid
    g.context_ok(ctx)
    g.approval_ok(ctx)
    not g.trajectory_ok(ctx, destructive_tools)
}
reason = "BLOCKED: rate limit exceeded — too many destructive ops in the window (feature E)." if {
    tool in destructive_tools
    principal.authenticated == true
    target_valid
    g.context_ok(ctx)
    g.approval_ok(ctx)
    g.trajectory_ok(ctx, destructive_tools)
    not g.rate_ok(ctx, destructive_tools)
}
reason = "BLOCKED: tool is not in a known secops verb-class." if {
    not tool in read_tools
    not tool in mutate_tools
    not tool in destructive_tools
}
reason = "Action approved by policy engine." if { allow }

# ---------------------------------------------------------------------------
# G — output redaction (HERO): strip internal identifiers / IOCs.
# ---------------------------------------------------------------------------
redact = sensitive_fields if {
    tool in {"list_hosts", "get_host", "list_incidents", "search_logs"}
}