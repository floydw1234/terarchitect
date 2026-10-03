"""Auto-ship finalize after compose and loud failures when shipper is unavailable."""

import os
from unittest.mock import patch

from models.db import Project, PromotionCandidate, ShipRun, Ticket, TicketAttempt, db


def _auto_ship_project_with_run(client):
    with client.application.app_context():
        project = Project(
            name="auto-ship-finalize",
            git_mode="swarm",
            ship_target="agenthub",
            auto_ship=True,
            accepted_frontier_id="f" * 40,
            shipped_frontier="f" * 40,
        )
        db.session.add(project)
        db.session.flush()

        ticket = Ticket(
            project_id=project.id,
            column_id="done",
            title="Finalize ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()

        attempt = TicketAttempt(
            project_id=project.id,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="f" * 40,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.flush()

        candidate = PromotionCandidate(
            project_id=project.id,
            selected_attempt_ids=[str(attempt.id)],
            selected_leaf_hashes=["a" * 40],
            base_root_hash="f" * 40,
            status="queued",
        )
        db.session.add(candidate)
        db.session.flush()

        run = ShipRun(
            project_id=project.id,
            promotion_candidate_id=candidate.id,
            status="composing",
        )
        db.session.add(run)
        db.session.commit()
        return str(project.id), str(run.id), "c" * 40


@patch("api.routes._ensure_commit_in_agenthub", lambda *a, **k: None)
def test_worker_composed_auto_finalizes_agenthub_auto_ship(client):
    pid, run_id, composed = _auto_ship_project_with_run(client)

    resp = client.post(
        f"/api/worker/ship-run/{run_id}/composed",
        json={
            "composed_commit_hash": composed,
            "base_main_hash": "f" * 40,
            "test_status": "passed",
            "changed_files": ["x.py"],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "shipped"
    assert data["shipped_commit_hash"] == composed

    with client.application.app_context():
        project = db.session.get(Project, pid)
        assert project.shipped_frontier == composed


def test_local_shipper_import_failure_records_ship_run_error(client):
    with client.application.app_context():
        project = Project(name="import-fail", git_mode="swarm", auto_ship=True)
        db.session.add(project)
        db.session.flush()
        run = ShipRun(project_id=project.id, status="queued")
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

        from api.services.promotion_spine_service import PromotionSpineError, _run_local_shipper_for_run

        real_import = __import__

        def _block_cli_shipper(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "cli._shipper":
                raise ImportError("No module named 'cli'")
            return real_import(name, globals, locals, fromlist, level)

        env = {
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_AUTH_DISABLED": "1",
            "TERARCHITECT_IN_CONTAINER": "1",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "builtins.__import__", side_effect=_block_cli_shipper
        ):
            try:
                _run_local_shipper_for_run(run_id)
                assert False, "expected PromotionSpineError"
            except PromotionSpineError as exc:
                assert "import failed" in str(exc).lower()

        stored = db.session.get(ShipRun, run_id)
        assert stored.status == "compose_failed"
        assert stored.error and "import failed" in stored.error.lower()
