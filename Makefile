.PHONY: test-smoke test-full test-swarm test-real test-swarm-docker test-clean help

COMPOSE      = docker compose -f docker-compose.yml -f docker-compose.test.yml --project-name terarchitect-test
COMPOSE_SWARM = $(COMPOSE) --profile swarm
# Clear ROS PYTHONPATH pollution so pytest doesn't pick up launch_testing etc.
PYTEST       = PYTHONPATH= backend/.venv/bin/pytest

# ---------------------------------------------------------------------------
# Test targets
# ---------------------------------------------------------------------------

## Run Tier 1 API smoke tests (starts backend+postgres, no agent/LLM)
test-smoke:
	$(COMPOSE) up -d --wait backend postgres
	$(PYTEST) tests/integration/test_api_smoke.py -m smoke -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 1 smoke tests against an ALREADY RUNNING backend (skips compose)
test-smoke-live:
	$(PYTEST) tests/integration/test_api_smoke.py -m smoke --no-compose -v

## Run Tier 2 full-stack tests (stub LLM + stub AgentHub + stub worker; starts backend+postgres)
test-full:
	$(COMPOSE) up -d --wait backend postgres
	$(PYTEST) tests/integration/test_swarm.py -m swarm -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 2 full-stack tests against an ALREADY RUNNING backend (skips compose)
test-full-live:
	$(PYTEST) tests/integration/test_swarm.py -m swarm --no-compose -v

## Run Tier 2 swarm tests (stub LLM + stub agenthub + stub worker; starts backend+postgres)
test-swarm:
	$(COMPOSE) up -d --wait backend postgres
	$(PYTEST) tests/integration/test_swarm.py -m swarm -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 2 swarm tests against an ALREADY RUNNING backend (skips compose)
test-swarm-live:
	$(PYTEST) tests/integration/test_swarm.py -m swarm --no-compose -v

## Run Tier 2b real-agenthub swarm tests (builds agenthub from source; requires go on PATH)
test-swarm-real:
	$(COMPOSE) up -d --wait backend postgres
	$(PYTEST) tests/integration/test_swarm_real.py -m swarm_real -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 2b real-agenthub tests against an ALREADY RUNNING backend
test-swarm-real-live:
	$(PYTEST) tests/integration/test_swarm_real.py -m swarm_real --no-compose -v

## Run Tier 2c swarm tests using the prod terarchitect-agenthub Docker image (rebuild first if needed)
test-swarm-docker:
	$(COMPOSE) up -d --wait backend postgres
	$(PYTEST) tests/integration/test_swarm_docker.py -m swarm_docker -v || true
	$(COMPOSE) down -v --remove-orphans

## Run Tier 2c Docker-agenthub tests against an ALREADY RUNNING backend (prod on 5010)
test-swarm-docker-live:
	$(PYTEST) tests/integration/test_swarm_docker.py -m swarm_docker --no-compose --api-url http://localhost:5010 -v

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
