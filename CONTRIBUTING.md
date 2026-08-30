# Contributing to Terarchitect

Thanks for helping make Terarchitect better. This project is currently **alpha**: the core workflow is working and dogfooded, but public APIs, deployment defaults, and operator flows may still change.

## What to work on

Good first contributions tend to be:

- documentation fixes
- fresh-clone quickstart improvements
- test coverage for Ship Room, AgentHub, coordinator, and CLI behavior
- small UI affordances that make failures easier to understand
- worker/runtime hardening that preserves the human review boundary

Please keep changes focused. Terarchitect is an orchestration system; broad refactors are easy to start and annoying to safely finish.

## Development setup

```bash
git clone https://github.com/floydw1234/terarchitect.git
cd terarchitect
cp .env.example .env
# Fill in only the keys needed for the workflow you are testing.
docker compose up -d
```

Useful local checks:

```bash
# Python focused smoke/unit checks
make pytest ARGS='\
  tests/test_cli_output.py \
  tests/test_cli_api_errors.py \
  backend/tests/test_unit.py \
  agent/tests/test_director_request_payload.py \
  coordinator/tests/test_fetch_max_concurrent.py'

# AgentHub
cd agenthub && go test ./...

# Frontend
cd frontend && npm test -- --watchAll=false --runInBand --silent
```

If `.venv` does not exist, create one and install all Python requirements first:

```bash
./scripts/bootstrap-python-env.sh
```

Do not run bare `pip install` for Terarchitect from Hermes or any other shared venv.

## Pull request expectations

A good PR includes:

- a clear summary of what changed and why
- the exact tests/checks run
- screenshots or clips for UI changes
- migration notes for behavior/config changes
- no real credentials, local databases, logs, or machine-specific paths

## Runtime and security boundaries

Terarchitect can run coding agents against real repositories. Please preserve these design principles:

- agents produce inspectable attempts
- humans accept or reject attempts
- shipping is a separate promotion boundary
- secrets stay in environment variables or secret stores, never in committed config
- worker logs should avoid printing token values

## Reporting issues

Use GitHub issues for bugs, docs gaps, and feature proposals. Include enough detail to reproduce the behavior: environment, command, expected result, actual result, and relevant redacted logs.
