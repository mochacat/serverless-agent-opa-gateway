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
  - **Done =** `opa check policies/` passes; `opa eval` runs on a fixture input. ✅ (verified: `opa check` pass; regopy in-process eval confirms all 6 branches — read/mutate/delete×prod/dev/unknown-tool + lease deny)
- [x] **T1.2** `policies/` — **multi-industry demo surface** (`base.rego` + `healthcare.rego` + `fintech.rego` + `secops.rego` + `policy.rego`), covering **all A–H**. **Heroes B/E/F/G** each produce a distinct `reason` (and `redact` for G). Same gate, different nouns/rules per industry (REQUIREMENTS.md §1.1); `policy.rego` dispatches on `input.industry` to the selected surface (unknown/empty industry → fail-closed default-deny). ✅ (verified via regopy: all 3 industries' allow/deny/reason/redact branches + fail-closed; fixed a rate-count dedup bug in `base.count_destructive`)
- [x] **T1.3** `policies/*_test.rego` — Rego unit tests (T1): default-deny, each hero, each deny/allow branch, per industry (healthcare/fintech/secops) + fail-closed dispatch.
  - **Done =** `opa test policies/ -v` green; **hero cases outnumber baseline cases** (§5.1 emphasis). ✅ (verified: **77/77 PASS** across `base_test` (17), `healthcare_test` (14), `fintech_test` (21), `secops_test` (17), `policy_test` (9); hero B/E/F/G + fail-closed cases outnumber baseline A/C/H per industry)

### 1b. Build + engine
- [x] **T2.1** Makefile `test-policy` (layers 1–2: `opa check policies/` + `opa test policies/ -v`). A `build-wasm` target is **optional/D5a-only** — the demo evaluates Rego directly via `regopy` (D5/D6), so no `.wasm` compile step in the default path.
  - **Done =** `make test-policy` runs both `opa check` and `opa test`; CI can add a `build-wasm` stage later without rework. ✅ (`make test-policy`: `opa check` + `opa test` 77/77 + layer-3 regopy load/eval smoke green; `PY` targets `.venv/bin/python` since `regopy` lives in the venv.)
- [x] **T2.2** `src/engine/policy_engine.py` — load `policies/` (all `.rego`) once via `regopy` at module-level (cold start); `evaluate(industry, tool, args, context, principal) -> Decision{allow, reason, redact}` dispatching on `input.industry`.
  - **Done =** engine returns correct decisions against T1.2's policy for a fixture input across all 3 industries + fail-closed unknown; loads once. ✅ (engine excludes `*_test.rego` from the runtime — those same-package test rules shadow `decision`; serializes `set_input`/`query` since the regopy Interpreter segfaults under concurrent eval.)
- [x] **T2.3** `src/engine/session_store.py` — in-memory, dependency-injected store for **D/E**: `trajectory_for(session_id)` + `record_event(...)`.
  - **Done =** append/read behavior unit-tested; exposed behind an injected interface (the interface, not a backing technology, is what matters). ✅ (`SessionStore` protocol + thread-safe `InMemorySessionStore` + `enrich_context_with_session`; append/read/rate + D/E end-to-end through the real engine tested.)
- [x] **T2.4** `tests/test_policy_engine.py` (T2) — engine unit tests: determinism, single-load, correct decision/reason.
  - **Done =** `pytest tests/test_policy_engine.py` green. ✅ (adds `tests/test_session_store.py`; **29/29** engine+store tests pass, ruff clean.)

### 1c. Gateway + audit
- [x] **T3.1** `src/gateway/app.py` — JSON-RPC `tools/call`; schema-validate → enrich principal → **evaluate** → build allow/deny (+ apply `redact` for G); **injected audit + metric sinks**.
  - **Done =** request in → decision out; JSON-RPC error on DENY; sinks injectable (O5/D8). ✅ (`GatewayApp.handle` transport-agnostic; trace_id from W3C `traceparent` else generated; JSON-RPC `id` echoed correctly — caught via live HTTP smoke.)
- [x] **T3.2** `src/audit/` — audit record builder: `{tool, args, principal, context, decision, policy_version, reason, trace_id}` (allow AND deny).
  - **Done =** record has all fields incl. `trace_id` (agent-propagated, else generated). ✅ (`src/audit/audit.py`: `build_audit_record` + `AuditSink` protocol + `StdoutAuditSink`/`InMemoryAuditSink`; also `src/metrics/metrics.py` injected counters per O5.)
- [x] **T3.3** `src/gateway/server.py` — local HTTP server via **stdlib `http.server`** (O4/D7) serving JSON-RPC; applies G redaction to returned output.
  - **Done =** `make run-gateway` serves; redaction strips listed fields from a sample output. ✅ (`python3 -m src.gateway.server` over ThreadingHTTPServer; live curl: /health ok, allow→`REDACTED`, deny→`-32000` with reason.)
- [x] **T3.4** `tests/test_gateway_unit.py` (T3) — schema rejection, identity enrichment, allow/deny response, audit written (fake sinks).
  - **Done =** unit tests green via injected fakes. ✅ (**19** gateway unit tests; full suite **49/49**, ruff clean.)

### 1d. Integration + test data
- [x] **T4.1** `tests/testdata/*.json` — fixtures: safe read, production-delete, unapproved-destructive, PHI query, trajectory sequence, rate-budget.
  - **Done =** fixtures load and exercise intended paths. ✅ (8 fixtures: safe_read, unapproved_destructive, production_delete [C blast-radius — the current surface has no `production` noun; the isolated/unleased guard stands in], phi_query [B deny], phi_redaction [B+G allow], trajectory_sequence [D steps], rate_budget [E], + prompt_injection_coercion [LLM01], secops_target_injection [B].)
- [x] **T4.2** `tests/test_gateway_integration.py` (T4) — **safe vs unsafe** end-to-end through real engine: read allowed, destructive-without-approval denied, production-delete denied, PHI redacted (G), trajectory enforced (D), rate enforced (E). **Heroes get the most scenarios** (§5.1).
  - **Done =** all assertions pass; no live AWS/Lambda; simulated tool output redacted where G applies. ✅ (**13** integration tests, real regopy eval + real InMemorySessionStore + in-memory sinks; heroes B/E/F/G dominate. Added `tests/test_gateway_server.py` — **6** HTTP round-trip tests. Full suite **68/68**, coverage **92%** ≥ 80% gate, ruff clean.)

### 1e. Framework adapters
> The gateway IS the product; these adapters are thin client-side props that *show the gateway
> working* with a real agent framework (allow/deny + re-plan on deny). LangChain first, Pydantic
> AI next. Frameworks stay optional/lazy imports — the gateway core is framework-free.

- [x] **T5.1** `src/agent_interceptor.py` — `OPAGuardrailTool`: wraps a LangChain `BaseTool`, gates every `invoke`/`ainvoke` through the gateway (`src/agent_gateway_client.py`), returns the policy reason on DENY (model re-plans), applies G-redaction on ALLOW. ✅ (`requirements-langchain.txt` optional extra; `make demo-langchain` shows a real gated tool: `get_balance` ALLOW, over-cap `execute_transfer` DENY with real Rego reason; 10 tests, skips cleanly when langchain absent.)
  - **Done =** works against the local server. ✅

---

## Phase 2 — Deployable infra (OpenTofu)

- [x] **T6.1** `infra/modules/agent_gateway/` — reusable module: Lambda + least-privilege IAM, **API Gateway HTTP API**, CloudWatch **log group** (audit retention); variables + outputs.
  - **Done =** `tofu validate` on the module passes; inputs/outputs per `DESIGN.md` §4.7. ✅ (8 files: variables.tf [core + SECURITY+COMPLIANCE+FINOPS inputs], locals.tf [mandatory tags + compliance retention floor], versions.tf, main_role.tf [least-privilege — only logs:CreateLogStream/PutLogEvents, scoped KMS], cloudwatch.tf [retention = max(requested, compliance floor)], lambda.tf [x86_64 — regopy only ships x86_64 wheels on PyPI; arm64 documented as the switch when aarch64 wheel is available], apigw.tf [HTTP API, throttling, CORS locked down], outputs.tf [gateway_url, tools_call_url, lambda_arn, audit_log_group_arn, etc.].)
- [x] **T6.2** `infra/envs/dev/` — runnable root config instantiating the module → `tofu apply`.
  - **Done =** `tofu fmt -check` + `tofu validate` pass; plan is apply-able (CI uses validate only; real apply is the manual deploy step). ✅ (4 files: main.tf [dev instantiates module with cheap defaults], variables.tf, outputs.tf, versions.tf [+ terraform.tfvars.example]. Both module + root config `tofu validate` green.)
- [x] **T6.3** Makefile — `build-zip` (Lambda package incl. `policies/*.rego` + code), `deploy`/`tofu apply` helper.
  - **Done =** `make build-zip` produces a deployable artifact; deploy path documented (`docs/deployment.md`). ✅ (`make build-zip` → `build/agent-gw.zip` ~44MB (23MB compressed zip sites; 250MB Lambda limit fine; >50MB needs S3 upload for deploy). `deploy` is `tofu apply` from infra/envs/dev/ + docs for the deploy path.)
- [x] **T6.0** (also built) `src/handler.py` + `tests/test_handler.py` — Lambda entrypoint adapting API Gateway HTTP API v2 events → GatewayApp → response.
  - **Done =** handler tests (8) pass; handler is the entrypoint the module's lambda.tf references. ([`src/handler.py`](src/handler.py) and [`tests/test_handler.py`](tests/test_handler.py) were missing from the TASKS list — added and verified to close the Lambda deploy gap.)

---

## Phase 3 — CI/CD (functional)

- [x] **T7.1** `.github/workflows/ci.yml` — stages: `opa check` → `opa test` → `ruff format --check`/`ruff check` → `pytest` + coverage gate → `tofu fmt -check` + `tofu validate`. Triggers: push + PR. (A `make build-wasm` stage is optional/D5a-only — not in the default pipeline.)
  - **Done =** workflow runs all stages and **fails on any broken policy/test/unformatted code**; deploy job manual via `workflow_dispatch`. ✅ (**Design decision: there is NO CI deploy job and CI holds no AWS credentials.** This is an OSS repo with no single target account — the artifact ships and each user runs `tofu apply` in *their own* account (see `docs/deployment.md`). CI = `opa check` → `opa test` → regopy smoke → `ruff format --check` → `ruff check` → `pytest`+cov gate → `tofu fmt -check` → `tofu validate` (module + dev env, init+validate only, zero AWS — verified locally that validate passes with no AWS creds). Superseded + removed the old `policy-validation.yml`. **All stage commands exercised locally green** (76 tests, 92% cov); the workflow itself is only truly provable on a real GitHub run after push.)
  - **Note:** the `deploy`/`plan`/`destroy` Makefile targets run `tofu` against **the user's own account** — they are manual helpers, not CI.

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
> A fresh clone reproduces: Rego tests green (T1), all pytest tiers green incl. coverage gate (T2–T6), local gateway demo serves decisions with zero AWS (N6), OpenTofu module validates/plans (N7), CI green on push/PR, and README quickstart runs end-to-end in ~10 seconds.
