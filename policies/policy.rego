# ai.guardrails — top-level dispatch for the serverless agent gateway
#
# The engine queries `data.ai.guardrails.decision`. This package dispatches on
# `input.industry` to the matching industry surface (healthcare / fintech /
# secops) and returns a single, stable decision object:
#   { "allow": bool, "reason": string, "redact": [string]? }
#
# The industry is supplied by the gateway (from client config or the enriched
# principal), never self-declared by the agent. Unknown industries are
# default-deny (fail closed).
package ai.guardrails

import future.keywords.in
import rego.v1

import data.ai.guardrails.healthcare as hc
import data.ai.guardrails.fintech as fi
import data.ai.guardrails.secops as so

industry := object.get(input, "industry", "")

# Allow: dispatch to the selected industry's decision. Unknown/empty industry
# is fail-closed: `allow` stays undefined → default-deny.
allow := hc.allow if {
    industry == "healthcare"
}
allow := fi.allow if {
    industry == "fintech"
}
allow := so.allow if {
    industry == "secops"
}

# Reason: the selected industry's reason, or fail-closed for unknown.
reason := hc.reason if {
    industry == "healthcare"
}
reason := fi.reason if {
    industry == "fintech"
}
reason := so.reason if {
    industry == "secops"
}
reason := "BLOCKED: unknown or unset industry — default-deny." if {
    not industry in {"healthcare", "fintech", "secops"}
}

# Redact: the selected industry's redact list (G).
redact := hc.redact if {
    industry == "healthcare"
}
redact := fi.redact if {
    industry == "fintech"
}
redact := so.redact if {
    industry == "secops"
}

# Stable output contract (DESIGN §4.3). Always defined: unknown industry →
# allow=false (undefined), fail-closed reason.
decision = {"allow": allow, "reason": reason} if { not redact }
decision = {"allow": allow, "reason": reason, "redact": redact} if { redact }
decision = {"allow": false, "reason": reason} if {
    not industry in {"healthcare", "fintech", "secops"}
    not redact
}