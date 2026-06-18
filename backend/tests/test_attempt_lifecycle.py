from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)


def test_completing_parallel_attempts_leaves_validated_attempts(client, project):
    from models.db import AgentJob, Ticket, TicketAttempt, db

    frontier_id = project["accepted_frontier_id"]
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="Parallel lifecycle ticket",
            intent_status="active",
            base_leaf_id=frontier_id,
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add_all([
            AgentJob(ticket_id=ticket.id, project_id=project["id"], kind="ticket", status="running"),
            AgentJob(ticket_id=ticket.id, project_id=project["id"], kind="ticket", status="pending"),
        ])
        db.session.commit()
        ticket_id = str(ticket.id)

    first = client.post(
        f"/api/projects/{project['id']}/tickets/{ticket_id}/complete",
        json={
            "commit_hash": "a" * 40,
            "base_hash": frontier_id,
            "agent_id": "parallel-1",
            "summary": "first validated attempt",
        },
    )
    second = client.post(
        f"/api/projects/{project['id']}/tickets/{ticket_id}/complete",
        json={
            "commit_hash": "b" * 40,
            "base_hash": frontier_id,
            "agent_id": "parallel-2",
            "summary": "second validated attempt",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200

    attempts = client.get(f"/api/projects/{project['id']}/tickets/{ticket_id}/attempts")
    assert attempts.status_code == 200
    payload = attempts.get_json()
    assert [attempt["status"] for attempt in payload] == ["validated", "validated"]
    assert all(attempt["validated"] is True for attempt in payload)
    assert all(attempt["is_winner"] is False for attempt in payload)
    assert all(attempt["integrated"] is False for attempt in payload)

    with client.application.app_context():
        stored_attempts = (
            TicketAttempt.query
            .filter_by(ticket_id=ticket_id)
            .order_by(TicketAttempt.attempt_num.asc())
            .all()
        )
        assert all(attempt.validated_at is not None for attempt in stored_attempts)
        assert all(attempt.is_winner is None for attempt in stored_attempts)
        assert all(attempt.integrated_at is None for attempt in stored_attempts)


def test_choose_winner_does_not_advance_frontiers(client, project):
    from models.db import Project, Ticket, TicketAttempt, db

    pid = project["id"]
    accepted_frontier = project["accepted_frontier_id"]
    shipped_frontier = "s" * 40

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_project.shipped_frontier = shipped_frontier
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Winner-only ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash=accepted_frontier,
            attempt_num=1,
            status="validated",
            validated_at=_now(),
            summary="validated only",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    choose = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/choose-winner"
    )
    assert choose.status_code == 200
    payload = choose.get_json()
    assert payload["status"] == "validated"
    assert payload["is_winner"] is True
    assert payload["integrated"] is False
    assert payload["accepted_frontier_id"] == accepted_frontier

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_attempt = db.session.get(TicketAttempt, attempt_id)
        assert stored_project.accepted_frontier_id == accepted_frontier
        assert stored_project.shipped_frontier == shipped_frontier
        assert stored_attempt.is_winner is True
        assert stored_attempt.integrated_at is None


def test_dependencies_unblock_only_after_winner_is_integrated(client, project):
    from api.services.ticket_service import dispatch_unblocked_queued
    from models.db import Project, Ticket, TicketAttempt, db

    pid = project["id"]
    frontier = project["accepted_frontier_id"]
    client.put(f"/api/projects/{pid}", json={"github_url": "https://github.com/example/repo"})

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_project.shipped_frontier = frontier
        parent = Ticket(
            project_id=pid,
            column_id="done",
            title="Parent",
            intent_status="active",
            base_leaf_id=frontier,
        )
        db.session.add(parent)
        db.session.flush()
        child = Ticket(
            project_id=pid,
            column_id="queued",
            title="Child",
            intent_status="ready",
            depends_on_ticket_ids=[str(parent.id)],
            base_leaf_id=frontier,
        )
        db.session.add(child)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash="d" * 40,
            base_hash=frontier,
            attempt_num=1,
            status="validated",
            validated_at=_now(),
            summary="validated parent attempt",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(parent.id)
        child_id = str(child.id)
        attempt_id = str(attempt.id)

    with client.application.app_context():
        dispatch_unblocked_queued(pid)
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "queued"

    choose = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/choose-winner"
    )
    assert choose.status_code == 200

    with client.application.app_context():
        dispatch_unblocked_queued(pid)
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "queued"

    accept = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/accept"
    )
    assert accept.status_code == 200

    with client.application.app_context():
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "in_progress"


def test_accept_allows_attempt_based_on_integrated_dependency_winner_without_frontier_advance(client, project):
    from models.db import Project, Ticket, TicketAttempt, db

    pid = project["id"]
    frontier = project["accepted_frontier_id"]

    with client.application.app_context():
        parent = Ticket(
            project_id=pid,
            column_id="done",
            title="Parent",
            intent_status="active",
            base_leaf_id=frontier,
        )
        db.session.add(parent)
        db.session.flush()
        child = Ticket(
            project_id=pid,
            column_id="done",
            title="Child",
            intent_status="active",
            depends_on_ticket_ids=[str(parent.id)],
            base_leaf_id=frontier,
        )
        db.session.add(child)
        db.session.flush()
        parent_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash="a" * 40,
            base_hash=frontier,
            attempt_num=1,
            status="accepted",
            validated_at=_now(),
            integrated_at=_now(),
            integrated_frontier_id="a" * 40,
            is_winner=True,
            winner_chosen_at=_now(),
            summary="integrated parent winner",
        )
        child_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=child.id,
            agenthub_commit_hash="b" * 40,
            base_hash="a" * 40,
            attempt_num=1,
            status="validated",
            validated_at=_now(),
            summary="validated child winner",
        )
        db.session.add_all([parent_attempt, child_attempt])
        db.session.commit()
        child_ticket_id = str(child.id)
        child_attempt_id = str(child_attempt.id)

    choose = client.post(
        f"/api/projects/{pid}/tickets/{child_ticket_id}/attempts/{child_attempt_id}/choose-winner"
    )
    assert choose.status_code == 200

    accept = client.post(
        f"/api/projects/{pid}/tickets/{child_ticket_id}/attempts/{child_attempt_id}/accept"
    )
    assert accept.status_code == 200
    payload = accept.get_json()
    assert payload["status"] == "accepted"
    assert payload["is_winner"] is True
    assert payload["integrated"] is True
    assert payload["accepted_frontier_id"] == frontier
    assert payload["integrated_frontier_id"] == "b" * 40

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_attempt = db.session.get(TicketAttempt, child_attempt_id)
        assert stored_project.accepted_frontier_id == frontier
        assert stored_attempt.integrated_frontier_id == "b" * 40
        assert stored_attempt.base_hash == "a" * 40


def test_promotion_candidate_blocks_non_integrated_or_non_winner_attempts(client, project):
    from models.db import Project, Ticket, TicketAttempt, db

    pid = project["id"]
    frontier = project["accepted_frontier_id"]
    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_project.shipped_frontier = frontier
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Candidate ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        validated_only = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="e" * 40,
            base_hash=frontier,
            attempt_num=1,
            status="validated",
            validated_at=_now(),
            summary="validated only",
        )
        db.session.add(validated_only)
        db.session.commit()
        attempt_id = str(validated_only.id)

    create_resp = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": [attempt_id]},
    )

    assert create_resp.status_code == 201
    data = create_resp.get_json()
    assert data["status"] == "blocked"
    assert "winning integrated attempt" in (data["conflict_summary"] or "")
