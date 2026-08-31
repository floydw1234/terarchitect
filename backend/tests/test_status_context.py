"""Status and context projection API tests."""
from unittest.mock import patch


def test_ticket_ledger_projects_existing_work_chain_and_evidence(client, project):
    pid = project["id"]

    from models.db import (
        db,
        AgentJob,
        EvidenceBundle,
        EvidenceCheck,
        EvidenceRun,
        Project,
        PromotionCandidate,
        ShipRun,
        Ticket,
        TicketAttempt,
    )

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="in_progress",
            title="Harden context surfaces",
            intent_status="active",
            acceptance_criteria="Operator can inspect ticket chain.",
        )
        db.session.add(ticket)
        db.session.flush()

        job = AgentJob(
            project_id=pid,
            ticket_id=ticket.id,
            status="completed",
            kind="ticket",
        )
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
            summary="Added status endpoint",
            test_status="passed",
        )
        db.session.add_all([job, attempt])
        db.session.flush()

        candidate = PromotionCandidate(
            project_id=pid,
            selected_attempt_ids=[str(attempt.id)],
            selected_leaf_hashes=["a" * 40],
            base_root_hash="b" * 40,
            status="composed",
            validation_summary={"blockers": []},
            composed_commit_hash="c" * 40,
        )
        db.session.add(candidate)
        db.session.flush()

        ship_run = ShipRun(
            project_id=pid,
            promotion_candidate_id=candidate.id,
            status="shipped",
            base_main_hash="b" * 40,
            composed_commit_hash="c" * 40,
            release_pr_url="https://github.example/pull/42",
            release_pr_number=42,
            shipped_commit_hash="d" * 40,
        )
        db.session.add(ship_run)
        db.session.flush()

        bundle = EvidenceBundle(
            project_id=pid,
            target_type="attempt",
            target_id=attempt.id,
            base_hash="b" * 40,
            candidate_hash="a" * 40,
            selected_attempt_ids=[str(attempt.id)],
            selected_leaf_hashes=["a" * 40],
            status="passed",
            risk_level="low",
            summary="Pytest evidence",
        )
        db.session.add(bundle)
        db.session.flush()

        check = EvidenceCheck(
            evidence_bundle_id=bundle.id,
            check_type="unit",
            status="passed",
            tool_name="pytest",
            command="pytest tests/test_cli_status_context.py",
            output="2 passed",
        )
        run = EvidenceRun(
            project_id=pid,
            evidence_bundle_id=bundle.id,
            run_type="suite",
            status="completed",
            target_type="attempt",
            target_id=attempt.id,
            check_type="unit",
            request_data={"suite": "status-context"},
        )
        db.session.add_all([check, run])

        db.session.commit()

        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)
        candidate_id = str(candidate.id)
        ship_run_id = str(ship_run.id)

    frontier_resp = client.post(f"/api/projects/{pid}/frontier", json={"hash": "d" * 40, "source": "manual"})
    assert frontier_resp.status_code == 200

    resp = client.get(f"/api/projects/{pid}/tickets/{ticket_id}/ledger")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ticket"]["id"] == ticket_id
    assert data["accepted_attempt"]["id"] == attempt_id
    assert data["promotion_candidate"]["id"] == candidate_id
    assert data["ship_run"]["id"] == ship_run_id
    assert data["evidence_summary"]["canonical_source"] == [
        "EvidenceBundle",
        "EvidenceRun",
        "EvidenceCheck",
    ]
    assert data["evidence_summary"]["bundle_count"] == 1
    assert data["evidence_summary"]["run_count"] == 1
    assert data["evidence_summary"]["check_counts"] == {"passed": 1}
    assert [item["kind"] for item in data["timeline"]] == [
        "ticket",
        "job",
        "attempt",
        "acceptance",
        "promotion_candidate",
        "ship_run",
        "pull_request",
        "frontier",
    ]
    assert data["timeline"][2]["id"] == attempt_id
    assert data["timeline"][6]["url"] == "https://github.example/pull/42"
    assert data["timeline"][7]["commit_hash"] == "d" * 40
    assert data["next_commands"][0].startswith(f"ta context {pid} --ticket ")


def test_ticket_context_includes_agent_channels_recent_events_and_recovery_hints(client, project):
    pid = project["id"]

    from models.db import db, AgentJob, ExecutionLog, PromotionCandidate, ShipRun, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Recover runner state",
            description="Surface channel and path hints.",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()

        job = AgentJob(
            project_id=pid,
            ticket_id=ticket.id,
            status="running",
            kind="ticket",
        )
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="e" * 40,
            base_hash="f" * 40,
            attempt_num=1,
            status="accepted",
            summary="Runner resumed work",
        )
        db.session.add_all([job, attempt])
        db.session.flush()

        candidate = PromotionCandidate(
            project_id=pid,
            selected_attempt_ids=[str(attempt.id)],
            selected_leaf_hashes=["e" * 40],
            base_root_hash="f" * 40,
            status="valid",
            validation_summary={"blockers": []},
        )
        db.session.add(candidate)
        db.session.flush()

        ship_run = ShipRun(
            project_id=pid,
            promotion_candidate_id=candidate.id,
            status="ready_to_ship",
            base_main_hash="f" * 40,
            composed_commit_hash="9" * 40,
        )
        log = ExecutionLog(
            project_id=pid,
            ticket_id=ticket.id,
            session_id="sess-1",
            step="plan",
            summary="Worker resumed in /tmp/terarchitect_runner_abc123",
            raw_output=(
                "cwd='/tmp/terarchitect_runner_abc123'\n"
                "artifact at /tmp/terarchitect_runner_abc123/plan/recovery.md\n"
            ),
            success=True,
        )
        db.session.add_all([ship_run, log])

        db.session.commit()

        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)
        candidate_id = str(candidate.id)
        ship_run_id = str(ship_run.id)

    project_update = client.put(
        f"/api/projects/{pid}",
        json={
            "project_path": "/repo/app",
            "execution_mode": "local",
            "source_type": "local_path",
        },
    )
    assert project_update.status_code == 200, project_update.get_json()

    fake_posts = [
        {
            "id": "post-1",
            "content": (
                '{"terarchitect_event":1,"type":"attempt_published","message":"Attempt 1 published",'
                '"metadata":{"attempt_id":"' + attempt_id + '"}}'
            ),
            "created_at": "2026-06-10T10:00:00+00:00",
        },
        {
            "id": "post-2",
            "content": "[feedback] retry with recovery artifact",
            "created_at": "2026-06-10T10:01:00+00:00",
        },
    ]

    with patch("api.routes._fetch_channel_posts", return_value=fake_posts):
        resp = client.get(f"/api/projects/{pid}/tickets/{ticket_id}/context?agent=true")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["project"]["id"] == pid
    assert data["ticket"]["id"] == ticket_id
    assert data["attempts"][0]["id"] == attempt_id
    assert data["candidate"]["id"] == candidate_id
    assert data["ship_run"]["id"] == ship_run_id
    assert data["channels"]["ticket"].startswith("ticket-")
    assert data["channels"]["candidate"].startswith("cand-")
    assert data["channels"]["project"].startswith("project-")
    assert len(data["recent_events"]) == 2
    assert data["recent_events"][0]["event_type"] == "attempt_published"
    assert data["recent_events"][1]["event_type"] == "human_feedback"
    assert data["paths"]["project_path"] == "/repo/app"
    assert "/tmp/terarchitect_runner_abc123" in data["paths"]["runner_workdir_hint"]
    assert any("recovery.md" in hint for hint in data["paths"]["recovery_artifact_hints"])
    assert data["worker_context"]["current_ticket"]["id"] == ticket_id
    assert data["next_commands"][0].startswith(f"ta status {pid} --ticket ")
