.PHONY: test-smoke test-full test-swarm test-real test-clean help

COMPOSE      = docker compose -f docker-compose.yml -f docker-compose.test.yml --project-name terarchitect-test
COMPOSE_SWARM = $(COMPOSE) --profile swarm

# ---------------------------------------------------------------------------
# Test targets
# ---------------------------------------------------------------------------

## Run Tier 1 API smoke tests (starts backend+postgres, no agent/LLM)
test-smoke:
	$(COMPOSE) up -d --wait backend postgres
	pytest tests/integration/test_api_smoke.py -m smoke -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 1 smoke tests against an ALREADY RUNNING backend (skips compose)
test-smoke-live:
	pytest tests/integration/test_api_smoke.py -m smoke --no-compose -v

## Run Tier 2 full-stack tests (stub LLM + mock gh + stub worker; starts backend+postgres)
test-full:
	$(COMPOSE) up -d --wait backend postgres
	pytest tests/integration/test_structured.py -m integration -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 2 full-stack tests against an ALREADY RUNNING backend (skips compose)
test-full-live:
	pytest tests/integration/test_structured.py -m integration --no-compose -v

## Run Tier 2 swarm tests (stub LLM + stub agenthub + stub worker; starts backend+postgres)
test-swarm:
	$(COMPOSE) up -d --wait backend postgres
	pytest tests/integration/test_swarm.py -m swarm -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 2 swarm tests against an ALREADY RUNNING backend (skips compose)
test-swarm-live:
	pytest tests/integration/test_swarm.py -m swarm --no-compose -v

## Run Tier 2b real-agenthub swarm tests (builds agenthub from source; requires go on PATH)
test-swarm-real:
	$(COMPOSE) up -d --wait backend postgres
	pytest tests/integration/test_swarm_real.py -m swarm_real -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 2b real-agenthub tests against an ALREADY RUNNING backend
test-swarm-real-live:
	pytest tests/integration/test_swarm_real.py -m swarm_real --no-compose -v

## Run Tier 3 real-LLM tests (requires ANTHROPIC_API_KEY + GITHUB_TOKEN)
test-real:
	@echo "Real tests not yet implemented"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

## Remove test containers and volumes
test-clean:
	$(COMPOSE) down -v --remove-orphans 2>/dev/null || true

## Print this help
help:
	@grep -E '^## ' Makefile | sed 's/## /  /'
