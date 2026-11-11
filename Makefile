# Serverless Agentic OPA Guardrails — build/test/run
#
# T2.1: test-policy runs layers 1-2 (opa check + opa test). The demo evaluates
#       Rego directly in-process via regopy (D5/D6), so there is no `.wasm`
#       compile step in the default path; `build-wasm` is scaffolding only.
#
# The engine's regopy load smoke (examples/verify/policy_smoke.py) is layer 3 and is
# invoked here too so `make test-policy` proves the whole runtime can parse
# and evaluate the exact policies the Lambda embeds.
#
# CI runs the same targets — "CI green == local green".

.PHONY: lint test-policy test-integration test-all run-gateway demo demo-langchain demo-pydantic demo-strands deploy destroy plan help precommit

POLICIES_DIR := policies
# Use the active Python interpreter (developer manages their own venv).
PY := python3
RUFF := $(PY) -m ruff
PRECOMMIT := $(PY) -m pre_commit

# ------------------------------------------------------------ pre-commit ----
precommit: ## Run all pre-commit hooks (ruff format + tofu fmt)
	$(PRECOMMIT) run --all-files

# ---------------------------------------------------------------- lint --
# Single source of truth for static analysis: CI's `lint` job runs exactly
# these two commands, so `make lint` green == CI lint green.
lint: ## ruff format check + ruff lint (src, tests, scripts, handler.py)
	$(RUFF) format --check src/ tests/ examples/ handler.py
	$(RUFF) check src/ tests/ examples/ handler.py

# ---------------------------------------------------------------- Layer 1-2 --
# T2.1 — Rego static check + semantic test suite. `opa test` owns correctness.
test-policy: ## Rego layers 1-2 (opa check + opa test) + layer 3 (regopy smoke)
	opa check $(POLICIES_DIR)/
	opa test $(POLICIES_DIR)/ -v
	$(PY) examples/verify/policy_smoke.py $(POLICIES_DIR)

# ---------------------------------------------------------------- pytest tiers
test-integration: ## pytest tiers T2-T6 (zero AWS, injected fakes)
	$(PY) -m pytest -v --cov=. --cov-report=term-missing

test-all: test-policy test-integration ## Everything green == CI green
	@echo "TEST-ALL PASS"

run-gateway: ## Local gateway demo (zero AWS) — implemented in T3.3
	$(PY) -m src.gateway.server

DEMO_LOG := /tmp/agent-gw-demo.log

demo: ## One-shot local demo: start the gateway, drive tool calls, stop it
	@echo "-> starting gateway on 127.0.0.1:8080 and running examples/verify/demo_agent.py"
	@$(PY) -m src.gateway.server --port 8080 > $(DEMO_LOG) 2>&1 & \
	  GW=$$!; \
	  trap 'kill $$GW 2>/dev/null' EXIT; \
	  sleep 2; \
	  $(PY) examples/verify/demo_agent.py http://127.0.0.1:8080; \
	  EXIT=$$?; \
	  kill $$GW 2>/dev/null; \
	  wait $$GW 2>/dev/null; \
	  echo "-> gateway log: $(DEMO_LOG)"; \
	  exit $$EXIT

demo-langchain: ## One-shot demo: a real LangChain agent's tools gated by the gateway
	@$(PY) -c "import langchain_core" 2>/dev/null || { \
	  echo "-> installing the optional LangChain extra"; \
	  $(PY) -m pip install -q -e .[langchain]; }
	@echo "-> starting gateway and running examples/langchain/tool_interceptor.py"
	@$(PY) -m src.gateway.server --port 8080 > $(DEMO_LOG) 2>&1 & \
	  GW=$$!; \
	  trap 'kill $$GW 2>/dev/null' EXIT; \
	  sleep 2; \
	  $(PY) examples/langchain/tool_interceptor.py --url http://127.0.0.1:8080; \
	  EXIT=$$?; \
	  kill $$GW 2>/dev/null; \
	  wait $$GW 2>/dev/null; \
	  exit $$EXIT

demo-pydantic: ## One-shot demo: a Pydantic AI agent gated by the gateway
	@$(PY) -c "import pydantic_ai" 2>/dev/null || { \
	  echo "-> installing the optional Pydantic AI extra"; \
	  $(PY) -m pip install -q -e .[pydantic]; }
	@echo "-> starting gateway and running examples/pydantic/agent_hooks.py"
	@$(PY) -m src.gateway.server --port 8080 > $(DEMO_LOG) 2>&1 & \
	  GW=$$!; \
	  trap 'kill $$GW 2>/dev/null' EXIT; \
	  sleep 2; \
	  $(PY) examples/pydantic/agent_hooks.py --url http://127.0.0.1:8080; \
	  EXIT=$$?; \
	  kill $$GW 2>/dev/null; \
	  wait $$GW 2>/dev/null; \
	  exit $$EXIT

demo-strands: ## One-shot demo: Strands Agents SDK gated by the gateway
	@$(PY) -c "import strands" 2>/dev/null || { \
	  echo "-> installing the optional Strands extra"; \
	  $(PY) -m pip install -q -e .[strands]; }
	@echo "-> starting gateway and running examples/strands/gateway_agent.py"
	@$(PY) -m src.gateway.server --port 8080 > $(DEMO_LOG) 2>&1 & \
	  GW=$$!; \
	  trap 'kill $$GW 2>/dev/null' EXIT; \
	  sleep 2; \
	  $(PY) examples/strands/gateway_agent.py --url http://127.0.0.1:8080; \
	  EXIT=$$?; \
	  kill $$GW 2>/dev/null; \
	  wait $$GW 2>/dev/null; \
	  exit $$EXIT

# -------------------------------------------------------------- deploy (manual)
# DEPLOY IS MANUAL AND RUNS IN *YOUR* AWS ACCOUNT. There is deliberately no CI
# deploy: this is an OSS project with no single target account. You must have
# AWS credentials for the account you deploy into, and (optionally) your own
# terraform.tfvars. See docs/deployment.md for the full path + smoke test.
BUILD_DIR := build/lambda
ENV_DIR := infra/envs/dev
# Policy version stamped into audit records — git describe (tag + commit + dirty
# flag). Falls back to "dev" if git is unavailable (CI without a checkout).
POLICY_VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")

deploy: ## Build Lambda zip and deploy to your AWS account (tofu apply) — T6.3
	@echo "→ building Lambda package (code + policies + runtime deps → $(BUILD_DIR))"
	@rm -rf $(BUILD_DIR)
	@mkdir -p $(BUILD_DIR)
	cp -r src policies handler.py $(BUILD_DIR)/
	$(PY) -m pip install -r requirements.txt \
	  --target $(BUILD_DIR) \
	  --only-binary=:all: \
	  --no-compile --quiet
	@echo "→ deploying $(ENV_DIR) to YOUR AWS account (manual, no CI)"
	@echo "   policy version: $(POLICY_VERSION)"
	cd $(ENV_DIR) && tofu init -input=false && tofu apply -auto-approve -var="policy_version=$(POLICY_VERSION)"
	@echo "→ smoke-test the deployed gateway:"
	cd $(ENV_DIR) && tofu output -raw tools_call_url | sed 's/^/   POST /'
	@echo "   curl -s -X POST \"$$(cd $(ENV_DIR) && tofu output -raw tools_call_url)\" \\"
	@echo "     -H 'Content-Type: application/json' \\"
	@echo "     -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"tool\":\"get_balance\",\"industry\":\"fintech\"}}'"

destroy: ## Tear down the manually-deployed stack (your account)
	cd $(ENV_DIR) && tofu destroy -auto-approve

plan: ## Preview the deploy (tofu plan) — needs AWS credentials
	cd $(ENV_DIR) && tofu init -input=false && tofu plan

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'