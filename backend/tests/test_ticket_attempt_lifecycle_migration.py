import importlib.util
from pathlib import Path
from unittest.mock import call, patch


def _load_revision_module():
    revision_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "020_ticket_attempt_lifecycle_columns.py"
    )
    spec = importlib.util.spec_from_file_location("rev020_ticket_attempt_lifecycle_columns", revision_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ticket_attempt_lifecycle_revision_adds_columns_idempotently():
    module = _load_revision_module()

    with patch.object(module.op, "execute") as execute:
        module.upgrade()

    execute.assert_has_calls(
        [
            call("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS validated_at TIMESTAMP"),
            call("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS is_winner BOOLEAN"),
            call("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS winner_chosen_at TIMESTAMP"),
            call("ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS integrated_at TIMESTAMP"),
            call(
                "ALTER TABLE ticket_attempts ADD COLUMN IF NOT EXISTS integrated_frontier_id VARCHAR(255)"
            ),
        ]
    )
    assert execute.call_count == 5
