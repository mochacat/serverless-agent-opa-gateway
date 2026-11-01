# policies/ — the gateway's authorization policy

This directory is the **policy surface** of the gateway — the security boundary. It is written in **OPA Rego**, evaluated in-process via `regopy`, and is the portable, versioned contract (N4). This README explains what's here and how it's tested in CI.

## What's here

| File | Role |
|------|------|
| `base.rego` | Shared, pure helpers: change-ticket approval (F), target context/state (C), trajectory preconditions (D), rate/deny-window (E), resource-id regex (B) |
| `healthcare.rego` | Healthcare demo surface — PHI content-gating (B), output redaction (G), verb-classes (A) |
| `fintech.rego` | Fintech demo surface — amount cap + beneficiary allowlist (B), dual-control approval (F), rate window (E) |
| `secops.rego` | Secops demo surface — target hostname/IP regex (B), isolation (C), default-deny (H) |
| `policy.rego` | Top-level dispatch on `input.industry` → `data.ai.guardrails.decision`; unknown/empty industry fails closed |
| `*_test.rego` | Native Rego unit tests (run by `opa test policies/`) |

The industry surfaces show the **same gate expressing different rules** (REQUIREMENTS.md §1.1) — a per-client policy is authored by adapting one of these surfaces; the engine, gateway, and dispatch are identical.

---

# How this is tested in CI

The policy is tested in **three layers**, mirroring CI (REQUIREMENTS.md §5.6) and the **no-drift guarantee** (N4): the Rego you test is the Rego you deploy — no compiled artifact in between.

---

## The three layers

| # | Layer | Tool | What it catches | When it runs |
|---|-------|------|-----------------|--------------|
| 1 | **Static check** | `opa check policies/` | Syntax errors, unsafe/unbound variables, Rego-v1 violations | CI step 1, `make test-policy` |
| 2 | **Rego unit tests** | `opa test policies/ -v` | Semantic correctness of each rule (A–H) and each allow/deny branch | CI step 2 (T1) |
| 3 | **In-process load smoke** | `regopy` loads `policies/` + evaluates fixtures | The *actual deployed eval path* (Python `regopy` parses the same Rego) | CI step 3, local dev |

**Why three layers?** `opa check` proves the Rego is *valid*; `opa test` proves the rules are *correct*; the `regopy` smoke proves the *runtime that Lambda actually uses* can parse and evaluate it. Each catches a different class of failure, and all three must pass for CI to be green.

---

## Layer 1 — static check

```bash
opa check policies/
```

Passes if every file is syntactically valid and every variable is safe (bound before use). This is the fastest gate — a policy that doesn't parse fails the build immediately. Also catches Rego-v1 rules (`if` keyword required before rule bodies).

## Layer 2 — Rego unit tests

Native Rego tests live in `policies/*_test.rego` (co-located with the policy, so `opa test policies/` picks them up):

```rego
# policies/fintech_test.rego
package ai.guardrails.fintech

import data.ai.guardrails.fintech as f

test_execute_transfer_allowed if {
    f.allow with input as {...approved money-movement input...}
}
test_execute_transfer_requires_ticket if {
    not f.allow with input as {...no-ticket input...}
}
```

Run:
```bash
opa test policies/ -v
```

**Emphasis (REQUIREMENTS.md §5.1):** hero capabilities **B/E/F/G** get the *most* cases and the *hardest* constraints (argument-inspection edge cases, rate-limit boundaries, change-ticket permutations, redaction completeness). Baseline A/C/H is tested for correctness but with fewer adversarial cases.

## Layer 3 — in-process load smoke (`regopy`)

The strongest guarantee: the same `regopy` engine embedded in the Lambda (T2.2) loads the **same `policies/` files** and evaluates a fixture input. This is a smoke check in CI, and the backbone of local dev verification:

```python
from regopy import Interpreter

interp = Interpreter()
for f in ["base.rego", "healthcare.rego", "fintech.rego", "secops.rego", "policy.rego"]:
    interp.add_module(f, open(f"policies/{f}").read())

interp.set_input({
    "industry": "healthcare",
    "tool": "query_phi",
    "args": {"scope": "all"},
    "principal": {"id": "svc", "authenticated": True},
    "context": {},
})
d = interp.query("data.ai.guardrails.decision").results[0][0]
assert d["allow"] == False  # fail-closed
```

A convenient one-liner for local dev (writes a temp policy tree + input, evaluates via regopy). This is what the integration suite (T4) and the running gateway both rely on — the real in-process eval, no mocking.

---

## Fail-closed invariant

Every test tier asserts the **policy-safety invariant** (REQUIREMENTS.md §5.1, §5.3): any real blast-radius failure **denies**, any benign scenario **allows**. Specifically:

- **Unknown / empty `industry`** → `decision = {allow: false, reason: "unknown or unset industry — default-deny"}`. The gateway never self-approves a request it can't classify.
- **Every destructive / money-movement action** requires all of **B/C/D/E/F** to pass; any missing approval, context, trajectory, or rate condition denies with a distinct reason.

---

## Known footguns (recorded so fixes aren't re-introduced)

- **Set vs array comprehension for counting.** `base.count_destructive` must count distinct destructive **calls**, not distinct tool **types**. A set comprehension (`count({ t | ... })`) collapses two identical tool names to one and under-counts the rate window; use an **array** comprehension (`count([ t | ... ])`). This was a real bug found during T1.2 verification.

---

## Mapping to CI (REQUIREMENTS.md §5.6)

| CI step | Command |
|---------|---------|
| 1. Policy static analysis | `opa check policies/` |
| 2. Rego unit tests | `opa test policies/ -v` |
| 3. `regopy` load smoke | load `policies/` + eval a fixture (above) |

All three run in CI on push + PR; the build **fails** on any broken policy, failing test, or policy that fails to load. Make targets: `make test-policy` (layers 1–2), `make test-integration` (layer 3 + gateway), `make test-all`.

**The live workflow** is `.github/workflows/policy-validation.yml` — it runs these three layers against the actual `policies/` in this repo on every push/PR touching `policies/`. Locally, layer 3 runs via `python scripts/policy_smoke.py policies/`.

> **No-drift (N4):** tests and the deployed Lambda consume the **same `policies/` files from git**. There is no `.wasm`/compiled artifact in the demo path (D5/D6, D5a) — so the tested policy *is* the deployed policy by construction.