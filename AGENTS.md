# Guidance for coding agents

Before you push or open a pull request, run the same checks CI will run:

```bash
make ci-python
# or: ./scripts/ci-python.sh
```

All Python unit/API tests in the CI **Python smoke/unit checks** job must pass locally first. That suite is much larger than a few targeted files agents often run during development.

If you change **agenthub** (Go) or the **frontend**, also run those jobs from [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

```bash
cd agenthub && go test ./...
cd frontend && npm ci && npm test -- --watchAll=false --runInBand --silent
```

Paste the pass summary (pytest / go test / npm test output) into your PR **Verification** section.
