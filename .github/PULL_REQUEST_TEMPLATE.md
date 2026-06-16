## Summary

-

## Verification

Paste the exact checks you ran, for example:

```bash
PYTHONPATH=backend:agent backend/.venv/bin/pytest -q tests/test_cli_output.py tests/test_cli_api_errors.py backend/tests/test_unit.py agent/tests/test_director_request_payload.py coordinator/tests/test_fetch_max_concurrent.py
cd agenthub && go test ./...
cd frontend && npm test -- --watchAll=false --runInBand --silent
```

## Screenshots / clips

Required for UI changes.

## Notes

- [ ] I did not commit real credentials, local databases, logs, or generated runtime artifacts.
- [ ] I updated docs/tests when behavior changed.
