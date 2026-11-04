# Serverless Agentic OPA Guardrails — build/test/run
#
# T2.1: test-policy runs layers 1-2 (opa check + opa test). The demo evaluates
#       Rego directly in-process via regopy (D5/D6), so there is no `.wasm`
#       compile step in the default path; `build-wasm` is scaffolding only.
#
# The engine's regopy load smoke (scripts/policy_smoke.py) is layer 3 and is
# invoked here too so `make test-policy` proves the whole runtime can parse
# and evaluate the exact policies the Lambda embeds.
#
# CI runs the same targets — "CI green == local green".

.PHONY: lint test-policy test-integration test-all run-gateway demo demo-langchain demo-pydantic build-zip deploy destroy plan help

POLICIES_DIR := policies
# Runtime + dev deps (regopy/boto3/pytest) are installed in `.venv` (see
# requirements*.txt). Use it so `make` matches the test/CI interpreter.
PY := .venv/bin/python
RUFF := $(PY) -m ruff

# ---------------------------------------------------------------- lint --
# Single source of truth for static analysis: CI's `lint` job runs exactly
# these two commands, so `make lint` green == CI lint green.
lint: ## ruff format check + ruff lint (src, tests, scripts)
	$(RUFF) format --check src/ tests/ scripts/
	$(RUFF) check src/ tests/ scripts/

# ---------------------------------------------------------------- Layer 1-2 --
# T2.1 — Rego static check + semantic test suite. `opa test` owns correctness.
test-policy: ## Rego layers 1-2 (opa check + opa test) + layer 3 (regopy smoke)
	opa check $(POLICIES_DIR)/
	opa test $(POLICIES_DIR)/ -v
	$(PY) scripts/policy_smoke.py $(POLICIES_DIR)

# ---------------------------------------------------------------- pytest tiers
test-integration: ## pytest tiers T2-T6 (zero AWS, injected fakes)
	$(PY) -m pytest -v --cov=src --cov-report=term-missing

test-all: test-policy test-integration ## Everything green == CI green
	@echo "TEST-ALL PASS"

run-gateway: ## Local gateway demo (zero AWS) — implemented in T3.3
	$(PY) -m src.gateway.server

DEMO_LOG := /tmp/agent-gw-demo.log

demo: ## One-shot local demo: start the gateway, drive 9 tool calls, stop it
	@echo "→ starting gateway on 127.0.0.1:8080 and running scripts/demo_agent.py"
	@$(PY) -m src.gateway.server --port 8080 > $(DEMO_LOG) 2>&1 & \
	  GW=$$!; \
	  trap 'kill $$GW 2>/dev/null' EXIT; \
	  sleep 2; \
	  $(PY) scripts/demo_agent.py http://127.0.0.1:8080; \
	  EXIT=$$?; \
	  kill $$GW 2>/dev/null; \
	  wait $$GW 2>/dev/null; \
	  echo "→ gateway log: $(DEMO_LOG)"; \
	  exit $$EXIT

demo-langchain: ## One-shot demo: a real LangChain agent's tools gated by the gateway
	@$(PY) -c "import langchain_core" 2>/dev/null || { \
	  echo "→ installing the optional LangChain extra (requirements-langchain.txt)"; \
	  $(PY) -m pip install -q -r requirements-langchain.txt; }
	@echo "→ starting gateway and running scripts/demo_langchain_agent.py"
	@$(PY) -m src.gateway.server --port 8080 > $(DEMO_LOG) 2>&1 & \
	  GW=$$!; \
	  trap 'kill $$GW 2>/dev/null' EXIT; \
	  sleep 2; \
	  $(PY) scripts/demo_langchain_agent.py --url http://127.0.0.1:8080; \
	  EXIT=$$?; \
	  kill $$GW 2>/dev/null; \
	  wait $$GW 2>/dev/null; \
	  exit $$EXIT

demo-pydantic: ## One-shot demo: a Pydantic AI agent gated by the gateway
	@$(PY) -c "import pydantic_ai" 2>/dev/null || { \
	  echo "→ installing the optional Pydantic AI extra (requirements-pydanticai.txt)"; \
	  $(PY) -m pip install -q -r requirements-pydanticai.txt; }
	@echo "→ starting gateway and running scripts/demo_pydantic_agent.py"
	@$(PY) -m src.gateway.server --port 8080 > $(DEMO_LOG) 2>&1 & \
	  GW=$$!; \
	  trap 'kill $$GW 2>/dev/null' EXIT; \
	  sleep 2; \
	  $(PY) scripts/demo_pydantic_agent.py --url http://127.0.0.1:8080; \
	  EXIT=$$?; \
	  kill $$GW 2>/dev/null; \
	  wait $$GW 2>/dev/null; \
	  exit $$EXIT

# -------------------------------------------------------------- Lambda package
BUILD_DIR := build/lambda
ZIP_FILE  := build/agent-gw.zip

build-zip: ## Deployable Lambda zip (code + policies + deps) — T6.3
	rm -rf $(BUILD_DIR) $(ZIP_FILE)
	mkdir -p $(BUILD_DIR)
	# 1. Package code + policies.
	cp -r src policies $(BUILD_DIR)/
	# 2. Install runtime deps (only pre-built wheels, matching the local arch
	#    which is x86_64 — same as the Lambda target; see lambda.tf note).
	$(PY) -m pip install -r requirements.txt \
	  --target $(BUILD_DIR) \
	  --only-binary=:all: \
	  --no-compile --quiet
	# 3. Produce the deployable archive.
	cd $(BUILD_DIR) && zip -q -r ../../$(ZIP_FILE) .
	@echo "✓ build-zip: $(ZIP_FILE)  ($$(du -h $(ZIP_FILE) | cut -f1))"

# -------------------------------------------------------------- deploy (manual)
# DEPLOY IS MANUAL AND RUNS IN *YOUR* AWS ACCOUNT. There is deliberately no CI
# deploy: this is an OSS project with no single target account. You must have
# AWS credentials for the account you deploy into, and (optionally) your own
# terraform.tfvars. See docs/deployment.md for the full path + smoke test.
ENV_DIR := infra/envs/dev

deploy: build-zip ## Manually deploy to your AWS account (tofu apply) — T6.3
	@echo "→ deploying $(ENV_DIR) to YOUR AWS account (manual, no CI)"
	cd $(ENV_DIR) && tofu init -input=false && tofu apply -auto-approve
	@echo "→ smoke-test the deployed gateway:"
	cd $(ENV_DIR) && tofu output -raw tools_call_url | sed 's/^/   POST /'
	@echo "   curl -s -X POST \"$$(cd $(ENV_DIR) && tofu output -raw tools_call_url)\" \\"
	@echo "     -H 'Content-Type: application/json' \\"
	@echo "     -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"tool\":\"get_balance\",\"industry\":\"fintech\"}}'"

destroy: ## Tear down the manually-deployed stack (your account)
	cd $(ENV_DIR) && tofu destroy -auto-approve

plan: build-zip ## Preview the deploy (tofu plan) — needs AWS credentials
	cd $(ENV_DIR) && tofu init -input=false && tofu plan

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'