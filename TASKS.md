# TASKS.md — Serverless Agentic OPA Guardrails

> Derived from `DESIGN.md` (§7 Work Breakdown) and `REQUIREMENTS.md`. Knock tasks off **one by one**; do not move on until the previous `Done =` criterion passes.
>
> **Whole-project definition of done:** a fresh clone → `make test-all` green, `make run-gateway` demo serves decisions locally with zero AWS, OpenTofu module validates/plans (a real deploy is possible), and CI is green on push/PR.

---

## Legend
- `[ ]` not started · `[~]` in progress · `[x]` done
- **Phase 0** scaffolding · **Phase 1** runnable core · **Phase 2** deployable infra · **Phase 3** CI · **Phase 4** docs & final gate

---

## Phase 0 — Scaffolding (artifact-first)

- [x] **T0.1** Repo structure and root files
  - `requirements.txt` (runtime: `boto3`, `regopy`, `requests`)
  - `requirements-dev.txt` (dev: `pytest`, `pytest-cov`, `ruff`)
  - `pyproject.toml` (pytest `testpaths`, coverage `fail_under` ≥ 80, ruff config)
  - `.gitignore` (ignore `.venv/`, `__pycache__/`, `*.zip`, terraform state/local)
  - dirs `policies/`, `src/{engine,gateway}`, `tests/testdata`, `docs/`, `infra/`
  - **Done =** `python -m pytest` runs (discovers 0 tests cleanly), `ruff check .` passes. ✅ (verified: pytest 0 collected clean, ruff all-pass, deps install on Python 3.14)

---

## Phase 1 — Runnable core (local, zero AWS)

### 1a. Policy
- [x] **T1.1** `policies/agent_guardrails.rego` — baseline slice: **H** (default-deny), **A** (verb-class), **C** (context/state), **B** (argument/payload). *(Superseded in T1.2 by the multi-industry surface; the file was removed. Kept for the record.)*
  - **Done =** `opa check policies/` passes; `opa eval -t wasm` runs on a fixture input. ✅ (verified: `opa check` pass; wasm eval returns `true` for allowed read; regopy in-process eval confirms all 6 branches — read/mutate/delete×prod/dev/unknown-tool + lease deny)
- [x] **T1.2** `policies/` — **multi-industry demo surface** (`base.rego` + `healthcare.rego` + `fintech.rego` + `secops.rego` + `policy.rego`), covering **all A–H**. **Heroes B/E/F/G** each produce a distinct `reason` (and `redact` for G). Same gate, different nouns/rules per industry (REQUIREMENTS.md §1.1); `policy.rego` dispatches on `input.industry` to the selected surface (unknown/empty industry → fail-closed default-deny). ✅ (verified via regopy: all 3 industries' allow/deny/reason/redact branches + fail-closed; fixed a rate-count dedup bug in `base.count_destructive`)
- [x] **T1.3** `policies/*_test.rego` — Rego unit tests (T1): default-deny, each hero, each deny/allow branch, per industry (healthcare/fintech/secops) + fail-closed dispatch.
  - **Done =** `opa test policies/ -v` green; **hero cases outnumber baseline cases** (§5.1 emphasis). ✅ (verified: **77/77 PASS** across `base_test` (17), `healthcare_test` (14), `fintech_test` (21), `secops_test` (17), `policy_test` (9); hero B/E/F/G + fail-closed cases outnumber baseline A/C/H per industry)

### 1b. Build + engine
- [ ] **T2.1** Makefile `test-policy` (layers 1–2: `opa check policies/` + `opa test policies/ -v`). A `build-wasm` target is **optional/D5a-only** — the demo evaluates Rego directly via `regopy` (D5/D6), so no `.wasm` compile step in the default path.
  - **Done =** `make test-policy` runs both `opa check` and `opa test`; CI can add a `build-wasm` stage later without rework.
- [ ] **T2.2** `src/engine/policy_engine.py` — load `policies/` (all `.rego`) once via `regopy` at module-level (cold start); `evaluate(industry, tool, args, context, principal) -> Decision{allow, reason, redact}` dispatching on `input.industry`.
  - **Done =** engine returns correct decisions against T1.2's policy for a fixture input across all 3 industries + fail-closed unknown; loads once.
- [ ] **T2.3** `src/engine/session_store.py` — in-memory, dependency-injected store for **D/E**: `trajectory_for(session_id)` + `record_event(...)`.
  - **Done =** append/read behavior unit-tested; exposed behind an injected interface (the interface, not a backing technology, is what matters).
- [ ] **T2.4** `tests/test_policy_engine.py` (T2) — engine unit tests: determinism, single-load, correct decision/reason.
  - **Done =** `pytest tests/test_policy_engine.py` green.

### 1c. Gateway + audit
- [ ] **T3.1** `src/gateway/app.py` — JSON-RPC `tools/call`; schema-validate → enrich principal → **evaluate** → build allow/deny (+ apply `redact` for G); **injected audit + metric sinks**.
  - **Done =** request in → decision out; JSON-RPC error on DENY; sinks injectable (O5/D8).
- [ ] **T3.2** `src/audit/` — audit record builder: `{tool, args, principal, context, decision, policy_version, reason, trace_id}` (allow AND deny).
  - **Done =** record has all fields incl. `trace_id` (agent-propagated, else generated).
- [ ] **T3.3** `src/gateway/server.py` — local HTTP server via **stdlib `http.server`** (O4/D7) serving JSON-RPC; applies G redaction to returned output.
  - **Done =** `make run-gateway` serves; redaction strips listed fields from a sample output.
- [ ] **T3.4** `tests/test_gateway_unit.py` (T3) — schema rejection, identity enrichment, allow/deny response, audit written (fake sinks).
  - **Done =** unit tests green via injected fakes.

### 1d. Integration + test data
- [ ] **T4.1** `tests/testdata/*.json` — fixtures: safe read, production-delete, unapproved-destructive, PHI query, trajectory sequence, rate-budget.
  - **Done =** fixtures load and exercise intended paths.
- [ ] **T4.2** `tests/test_gateway_integration.py` (T4) — **safe vs unsafe** end-to-end through real engine: read allowed, destructive-without-approval denied, production-delete denied, PHI redacted (G), trajectory enforced (D), rate enforced (E). **Heroes get the most scenarios** (§5.1).
  - **Done =** all assertions pass; no live AWS/Lambda; simulated tool output redacted where G applies.

### 1e. Interceptor
- [ ] **T5.1** `src/agent_interceptor.py` — `OPAGuardrailInterceptor` for LangChain `BaseTool`: calls the gateway, returns reason to the LLM on DENY.
  - **Done =** works against the local server (documented example in README at T8.1).

---

## Phase 2 — Deployable infra (OpenTofu)

- [ ] **T6.1** `infra/modules/agent_gateway/` — reusable module: Lambda + least-privilege IAM, **API Gateway HTTP API**, CloudWatch **log group** (audit retention); variables + outputs.
  - **Done =** `tofu validate` on the module passes; inputs/outputs per `DESIGN.md` §4.7.
- [ ] **T6.2** `infra/envs/dev/` — runnable root config instantiating the module → `tofu apply`.
  - **Done =** `tofu fmt -check` + `tofu validate` pass; plan is apply-able (CI uses validate only; real apply is the manual deploy step).
- [ ] **T6.3** Makefile — `build-zip` (Lambda package incl. compiled `.wasm` + code), `deploy`/`tofu apply` helper.
  - **Done =** `make build-zip` produces a deployable artifact; deploy path documented (`docs/deployment.md`).

---

## Phase 3 — CI/CD (functional)

- [ ] **T7.1** `.github/workflows/ci.yml` — stages: `opa check` → `opa test` → `make build-wasm` → `ruff format --check`/`ruff check` → `pytest` + coverage gate → `tofu fmt -check` + `tofu validate`. Triggers: push + PR.
  - **Done =** workflow runs all stages and **fails on any broken policy/test/wasm/unformatted code**; deploy job manual via `workflow_dispatch`.

---

## Phase 4 — Docs & final gate

- [ ] **T8.1** Rewrite `README.md` — *why API Gateway → Lambda for multi-agent* (cost + spiky + stateless-MCP), quickstart (`make`), hero-capabilities callout (B/E/F/G), industry positioning, OWASP-covered items, "what this is / isn't" boundaries. **Remove stale content** (old "replication lag / B2B-resume" text).
  - **Done =** README matches the built artifact; quickstart runs end-to-end in ~10s.
- [ ] **T8.2** `docs/architecture.md` — deployment model, managed-vs-custom (Cedar/Guardrails vs OPA), latency honesty, module/env layout.
- [ ] **T8.3** `docs/security.md` — OWASP GenAI LLM Top-10 (2026) mapping + threat boundaries; never overclaiming.
- [ ] **T8.4** `docs/observability.md` — OTel adapter seam: `policy_decision` span schema, inbound-parent trace extraction, OTLP export (vendor-agnostic), async/off-critical-path.
- [ ] **T8.5** `docs/deployment.md` — local vs real deploy, cold-start/latency notes, module input/output reference, post-apply smoke test.
- [ ] **T8.6** `docs/compliance.md` — HIPAA/SOC2/ISO control-family mapping presented as *supports*, not certification; explicit scope boundaries.
- [ ] **T8.7** **Final gate** — fresh clone → `make test-all` green, `make run-gateway` demo works (zero AWS), CI green, README quickstart reproducible.
  - **Done =** whole-project definition of done passes on a clean checkout.

---

## Definition of Done (whole project)
> A fresh clone reproduces: Rego tests green (T1), `make build-wasm` works, all pytest tiers green incl. coverage gate (T2–T6), local gateway demo serves decisions with zero AWS (N6), OpenTofu module validates/plans (N7), CI green on push/PR, and README quickstart runs end-to-end in ~10 seconds.