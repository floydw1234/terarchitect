"""Phase 14 evidence bundle API tests."""
import json
import socket
import subprocess
import sys
from unittest.mock import patch


def test_create_evidence_bundle_and_add_check(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Evidence target",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    create_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "base_hash": "b" * 40,
        "candidate_hash": "a" * 40,
        "selected_attempt_ids": [attempt_id],
        "selected_leaf_hashes": ["a" * 40],
        "status": "collecting",
        "risk_level": "unknown",
        "summary": "Initial attempt evidence",
    })

    assert create_resp.status_code == 201
    bundle = create_resp.get_json()
    assert bundle["target_type"] == "attempt"
    assert bundle["target_id"] == attempt_id
    assert bundle["checks"] == []

    check_resp = client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/checks", json={
        "check_type": "unit",
        "status": "passed",
        "tool_name": "pytest",
        "command": "pytest backend/tests/test_unit.py",
        "output": "20 passed",
        "metadata": {"duration_seconds": 2.5},
    })

    assert check_resp.status_code == 201
    check = check_resp.get_json()
    assert check["check_type"] == "unit"
    assert check["status"] == "passed"
    assert check["metadata"]["duration_seconds"] == 2.5

    detail_resp = client.get(f"/api/projects/{pid}/evidence/{bundle['id']}")

    assert detail_resp.status_code == 200
    detail = detail_resp.get_json()
    assert detail["check_counts"] == {"passed": 1}
    assert detail["checks"][0]["tool_name"] == "pytest"


def test_evidence_list_filters_by_target_and_check_type(client, project):
    pid = project["id"]

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(project_id=pid, status="ready_to_ship")
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    bundle_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "ship_run",
        "target_id": run_id,
        "candidate_hash": "c" * 40,
        "status": "passed",
        "risk_level": "low",
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]

    check_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "integration",
        "status": "passed",
    })
    assert check_resp.status_code == 201

    by_target = client.get(
        f"/api/projects/{pid}/evidence?target_type=ship_run&target_id={run_id}"
    )
    by_check = client.get(f"/api/projects/{pid}/evidence?check_type=integration")

    assert by_target.status_code == 200
    assert [b["id"] for b in by_target.get_json()] == [bundle_id]
    assert by_check.status_code == 200
    assert [b["id"] for b in by_check.get_json()] == [bundle_id]


def test_evidence_bundle_validation_errors(client, project):
    pid = project["id"]

    resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "invalid",
        "target_id": "00000000-0000-0000-0000-000000000001",
    })

    assert resp.status_code == 400
    assert "target_type" in resp.get_json()["error"]


def test_verification_policy_blocks_missing_evidence(client, project):
    pid = project["id"]
    target_id = "aaaaaaaa-0000-0000-0000-000000000001"

    policy_resp = client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit", "integration"],
        "optional_checks": ["security"],
        "required_llm_reviewers": [],
        "block_on": ["missing_evidence", "failing_required_tests"],
    })
    eval_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    )

    assert policy_resp.status_code == 200
    assert eval_resp.status_code == 200
    data = eval_resp.get_json()
    assert data["allowed"] is False
    assert data["bundle"] is None
    assert data["required_checks"]["unit"]["status"] == "missing"
    assert "No evidence bundle exists" in data["reasons"][0]


def test_verification_policy_explains_required_check_failure_and_pass(client, project):
    pid = project["id"]
    target_id = "bbbbbbbb-0000-0000-0000-000000000002"

    policy_resp = client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit", "integration"],
        "block_on": ["missing_evidence", "failing_required_tests"],
    })
    assert policy_resp.status_code == 200

    bundle_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": target_id,
        "status": "passed",
        "risk_level": "low",
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]

    unit_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "unit",
        "status": "passed",
    })
    failed_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "integration",
        "status": "failed",
    })
    assert unit_resp.status_code == 201
    assert failed_resp.status_code == 201

    blocked_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    )
    assert blocked_resp.status_code == 200
    blocked = blocked_resp.get_json()
    assert blocked["allowed"] is False
    assert blocked["required_checks"]["unit"]["status"] == "passed"
    assert blocked["required_checks"]["integration"]["status"] == "failed"
    assert "integration" in blocked["reasons"][0]

    assert client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "integration",
        "status": "passed",
    }).status_code == 201

    allowed_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    )
    assert allowed_resp.status_code == 200
    allowed = allowed_resp.get_json()
    assert allowed["allowed"] is True
    assert allowed["reasons"] == []


def test_verification_policy_allows_required_check_waiver(client, project):
    pid = project["id"]
    target_id = "cccccccc-0000-0000-0000-000000000003"

    assert client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["security"],
        "block_on": ["missing_evidence", "failing_required_tests"],
    }).status_code == 200
    bundle_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": target_id,
        "status": "failed",
        "risk_level": "high",
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]
    assert client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "security",
        "status": "failed",
        "output": "known acceptable risk",
    }).status_code == 201

    blocked_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    )
    assert blocked_resp.status_code == 200
    assert blocked_resp.get_json()["allowed"] is False

    waiver_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/waivers", json={
        "check_type": "security",
        "actor": "alice",
        "reason": "Accepted for internal-only prototype.",
    })
    assert waiver_resp.status_code == 201
    waiver = waiver_resp.get_json()
    assert waiver["status"] == "warning"
    assert waiver["metadata"]["waiver"] is True
    assert waiver["metadata"]["actor"] == "alice"

    allowed_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    )
    assert allowed_resp.status_code == 200
    allowed = allowed_resp.get_json()
    assert allowed["allowed"] is True
    assert allowed["reasons"] == []
    assert allowed["required_checks"]["security"]["status"] == "waived"
    assert allowed["required_checks"]["security"]["passed"] is True
    assert allowed["required_checks"]["security"]["waiver"]["metadata"]["actor"] == "alice"


def test_verification_policy_requires_llm_reviewer(client, project):
    pid = project["id"]
    target_id = "abababab-0000-0000-0000-000000000004"

    assert client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_llm_reviewers": ["security_reviewer"],
        "block_on": ["missing_evidence", "failing_required_tests"],
    }).status_code == 200
    bundle_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": target_id,
        "status": "passed",
        "risk_level": "low",
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]

    missing_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    )
    assert missing_resp.status_code == 200
    missing = missing_resp.get_json()
    assert missing["allowed"] is False
    assert missing["required_llm_reviewers"]["security_reviewer"]["status"] == "missing"

    failed_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "llm_review",
        "status": "failed",
        "tool_name": "security_reviewer",
        "metadata": {
            "llm_review": True,
            "reviewer": "security_reviewer",
            "findings": [{
                "severity": "high",
                "claim": "SQL query uses untrusted input.",
                "blocking": True,
            }],
        },
    })
    assert failed_resp.status_code == 201

    failed_policy = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    ).get_json()
    assert failed_policy["allowed"] is False
    assert failed_policy["required_llm_reviewers"]["security_reviewer"]["status"] == "failed"

    assert client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "llm_review",
        "status": "passed",
        "tool_name": "security_reviewer",
        "metadata": {
            "llm_review": True,
            "reviewer": "security_reviewer",
            "findings": [],
        },
    }).status_code == 201

    allowed = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=attempt&target_id={target_id}"
    ).get_json()
    assert allowed["allowed"] is True
    assert allowed["required_llm_reviewers"]["security_reviewer"]["status"] == "passed"
    assert allowed["reasons"] == []


def test_evidence_waiver_validation_errors(client, project):
    pid = project["id"]
    bundle = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": "dddddddd-0000-0000-0000-000000000004",
        "status": "warning",
        "risk_level": "medium",
    }).get_json()

    resp = client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/waivers", json={
        "check_type": "unit",
    })

    assert resp.status_code == 400
    assert "reason" in resp.get_json()["error"]


def test_verification_policy_requires_human_approval_reference(client, project):
    pid = project["id"]
    target_id = "eeeeeeee-0000-0000-0000-000000000005"

    assert client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit"],
        "block_on": ["missing_evidence", "failing_required_tests", "missing_human_approval"],
    }).status_code == 200
    bundle_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "ship_run",
        "target_id": target_id,
        "status": "passed",
        "risk_level": "low",
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]
    assert client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "unit",
        "status": "passed",
    }).status_code == 201

    blocked_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=ship_run&target_id={target_id}"
    )
    assert blocked_resp.status_code == 200
    blocked = blocked_resp.get_json()
    assert blocked["allowed"] is False
    assert blocked["human_approval"] is None
    assert "Human approval" in blocked["reasons"][0]

    approval_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/approvals", json={
        "actor": "release-manager",
        "reason": "Reviewed evidence and approved export.",
    })
    assert approval_resp.status_code == 201
    approval = approval_resp.get_json()
    assert approval["status"] == "passed"
    assert approval["check_type"] == "human_approval"
    assert approval["metadata"]["approval"] is True
    assert approval["metadata"]["approved_bundle_id"] == bundle_id

    allowed_resp = client.get(
        f"/api/projects/{pid}/evidence/policy?target_type=ship_run&target_id={target_id}"
    )
    assert allowed_resp.status_code == 200
    allowed = allowed_resp.get_json()
    assert allowed["allowed"] is True
    assert allowed["reasons"] == []
    assert allowed["human_approval"]["metadata"]["actor"] == "release-manager"


def test_evidence_approval_validation_errors(client, project):
    pid = project["id"]
    bundle = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": "ffffffff-0000-0000-0000-000000000006",
        "status": "passed",
        "risk_level": "low",
    }).get_json()

    resp = client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/approvals", json={
        "actor": "alice",
    })

    assert resp.status_code == 400
    assert "reason" in resp.get_json()["error"]


def test_evidence_repair_creates_ticket_and_posts_event(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Broken feature", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    bundle_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "status": "failed",
        "risk_level": "high",
        "summary": "Unit regression",
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]
    check_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "unit",
        "status": "failed",
        "tool_name": "pytest",
        "command": "pytest",
        "output": "assert 1 == 2",
        "artifact_url": "artifact://pytest",
    })
    assert check_resp.status_code == 201
    failed_check_id = check_resp.get_json()["id"]

    with patch("api.services.evidence_service._post_event") as post_event:
        repair_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/repair", json={})

    assert repair_resp.status_code == 201
    repair = repair_resp.get_json()
    assert repair["created_source"] == "evidence_repair"
    assert repair["priority"] == "high"
    assert "Repair failed evidence" in repair["title"]
    assert "assert 1 == 2" in repair["description"]
    assert "unit" in repair["acceptance_criteria"]
    posted_channels = [call.args[0] for call in post_event.call_args_list]
    assert any(channel.startswith("project-") for channel in posted_channels)
    assert any(ticket_id.replace("-", "")[:24] in channel for channel in posted_channels)

    detail = client.get(f"/api/projects/{pid}/evidence/{bundle_id}").get_json()
    repair_checks = [c for c in detail["checks"] if c["check_type"] == "repair"]
    assert repair_checks
    assert repair_checks[0]["metadata"]["repair_ticket_id"] == repair["id"]
    assert repair_checks[0]["metadata"]["failing_check_ids"] == [failed_check_id]


def test_evidence_repair_can_auto_dispatch_repair_job(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-auto-repair",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt, AgentJob
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Broken feature", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    bundle_resp = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "status": "failed",
        "risk_level": "high",
    })
    bundle_id = bundle_resp.get_json()["id"]
    client.post(f"/api/projects/{pid}/evidence/{bundle_id}/checks", json={
        "check_type": "unit",
        "status": "failed",
        "tool_name": "pytest",
        "output": "assert 1 == 2",
    })

    repair_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/repair", json={
        "auto_dispatch_repair": True,
        "max_repair_attempts": 1,
    })

    assert repair_resp.status_code == 201
    repair = repair_resp.get_json()
    assert repair["column_id"] == "queued"
    with client.application.app_context():
        jobs = AgentJob.query.filter_by(ticket_id=repair["id"]).all()
        assert len(jobs) == 1
        assert jobs[0].status == "pending"
    detail = client.get(f"/api/projects/{pid}/evidence/{bundle_id}").get_json()
    repair_check = [c for c in detail["checks"] if c["check_type"] == "repair"][-1]
    dispatch = repair_check["metadata"]["repair_dispatch"]
    assert dispatch["dispatch_status"] == "enqueued"
    assert dispatch["job_id"]
    assert repair_check["metadata"]["repair_policy"]["max_attempts"] == 1

    duplicate_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/repair", json={
        "auto_dispatch_repair": True,
        "max_repair_attempts": 1,
    })
    assert duplicate_resp.status_code == 400
    assert "retry policy exhausted" in duplicate_resp.get_json()["error"]


def test_evidence_repair_rejects_passing_bundle(client, project):
    pid = project["id"]
    bundle = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": "abababab-0000-0000-0000-000000000012",
        "status": "passed",
        "risk_level": "low",
    }).get_json()

    repair_resp = client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/repair", json={})

    assert repair_resp.status_code == 400
    assert "Repair requires" in repair_resp.get_json()["error"]


def test_evidence_repair_converts_llm_blocker_to_fix_intent(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Auth change", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    bundle_resp = client.post(f"/api/projects/{pid}/evidence/run-llm-review", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "reviewer": "security_reviewer",
        "findings": [{
            "severity": "critical",
            "path": "backend/api/auth.py",
            "line": 42,
            "claim": "Admin route allows anonymous access",
            "evidence": "No authentication guard is applied before mutation.",
            "suggested_fix": "Require authenticated admin role before updating settings.",
            "blocking": True,
            "confidence": 0.91,
        }],
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]

    repair_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/repair", json={})

    assert repair_resp.status_code == 201
    repair = repair_resp.get_json()
    assert repair["created_source"] == "evidence_repair"
    assert "Admin route allows anonymous access" in repair["title"]
    assert "backend/api/auth.py:42" in repair["description"]
    assert "Require authenticated admin role" in repair["acceptance_criteria"]
    detail = client.get(f"/api/projects/{pid}/evidence/{bundle_id}").get_json()
    repair_check = [c for c in detail["checks"] if c["check_type"] == "repair"][-1]
    findings = repair_check["metadata"]["repair_findings"]
    assert findings[0]["check_type"] == "llm_review"
    assert findings[0]["suggested_fix"] == "Require authenticated admin role before updating settings."


def test_evidence_repair_converts_test_adequacy_blocker_to_fix_intent(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Password reset", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    bundle_resp = client.post(f"/api/projects/{pid}/evidence/run-test-adequacy", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "findings": [{
            "severity": "high",
            "criterion": "Reject expired reset tokens",
            "test_path": "backend/tests/test_password_reset.py",
            "covered": False,
            "claim": "Generated tests do not cover expired token rejection.",
            "evidence": "Only happy-path reset test exists.",
            "suggested_fix": "Add an expired-token test that expects HTTP 400.",
            "blocking": True,
        }],
    })
    assert bundle_resp.status_code == 201
    bundle_id = bundle_resp.get_json()["id"]

    repair_resp = client.post(f"/api/projects/{pid}/evidence/{bundle_id}/repair", json={})

    assert repair_resp.status_code == 201
    repair = repair_resp.get_json()
    assert "Generated tests do not cover expired token rejection" in repair["title"]
    assert "Reject expired reset tokens" in repair["acceptance_criteria"]
    assert "Add an expired-token test" in repair["acceptance_criteria"]
    detail = client.get(f"/api/projects/{pid}/evidence/{bundle_id}").get_json()
    repair_check = [c for c in detail["checks"] if c["check_type"] == "repair"][-1]
    findings = repair_check["metadata"]["repair_findings"]
    assert findings[0]["check_type"] == "test_adequacy"
    assert findings[0]["criterion"] == "Reject expired reset tokens"


def test_verification_policy_validation_errors(client, project):
    resp = client.put(f"/api/projects/{project['id']}/verification-policy", json={
        "required_checks": "unit",
    })

    assert resp.status_code == 400
    assert "required_checks" in resp.get_json()["error"]


def test_verification_policy_accepts_configured_check_suites(client, project):
    pid = project["id"]

    resp = client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit"],
        "check_suites": [
            {
                "check_type": "unit",
                "command": [sys.executable, "-c", "print('suite ok')"],
                "timeout_seconds": 30,
                "tool_name": "python",
            }
        ],
    })

    assert resp.status_code == 200
    policy = resp.get_json()
    assert policy["check_suites"][0]["check_type"] == "unit"
    assert policy["check_suites"][0]["command"][0] == sys.executable


def test_verification_policy_rejects_invalid_check_suite(client, project):
    resp = client.put(f"/api/projects/{project['id']}/verification-policy", json={
        "check_suites": [{"command": "pytest"}],
    })

    assert resp.status_code == 400
    assert "check_type" in resp.get_json()["error"]


def test_workspace_bless_requires_policy_evidence_when_configured(client, project):
    pid = project["id"]

    client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit"],
        "block_on": ["missing_evidence", "failing_required_tests"],
    })

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    blocked = client.post(f"/api/projects/{pid}/workspaces/{ws_id}/bless", json={})

    assert blocked.status_code == 409
    body = blocked.get_json()
    assert body["target_type"] == "composite_workspace"
    assert body["evidence_policy"]["allowed"] is False

    bundle = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "status": "passed",
        "risk_level": "low",
    }).get_json()
    assert client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/checks", json={
        "check_type": "unit",
        "status": "passed",
    }).status_code == 201

    allowed = client.post(f"/api/projects/{pid}/workspaces/{ws_id}/bless", json={})

    assert allowed.status_code == 200
    assert allowed.get_json()["status"] == "blessed"


def test_ship_requires_policy_evidence_when_configured(client, project, accepted_ticket_and_attempt):
    pid = project["id"]

    client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit"],
        "block_on": ["missing_evidence", "failing_required_tests"],
    })

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            status="ready_to_ship",
            composed_commit_hash="c" * 40,
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    blocked = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert blocked.status_code == 409
    body = blocked.get_json()
    assert body["target_type"] == "ship_run"
    assert body["target_id"] == run_id

    bundle = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "ship_run",
        "target_id": run_id,
        "status": "passed",
        "risk_level": "low",
    }).get_json()
    assert client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/checks", json={
        "check_type": "unit",
        "status": "passed",
    }).status_code == 201

    allowed = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert allowed.status_code == 200
    assert allowed.get_json()["status"] == "shipped"


def test_collect_workspace_evidence_from_composer_results(client, project):
    pid = project["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            base_root_hash="b" * 40,
            selected_attempt_ids=["aaaaaaaa-0000-0000-0000-000000000001"],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            changed_files=["src/app.py"],
            test_status="passed",
            test_output="1 passed",
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    resp = client.post(f"/api/projects/{pid}/evidence/collect", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "check_type": "unit",
    })

    assert resp.status_code == 201
    bundle = resp.get_json()
    assert bundle["target_type"] == "composite_workspace"
    assert bundle["base_hash"] == "b" * 40
    assert bundle["candidate_hash"] == "c" * 40
    assert bundle["status"] == "passed"
    assert bundle["risk_level"] == "low"
    assert bundle["checks"][0]["status"] == "passed"
    assert bundle["checks"][0]["metadata"]["changed_files"] == ["src/app.py"]


def test_collect_ship_run_evidence_allows_policy_gated_ship(client, project, accepted_ticket_and_attempt):
    pid = project["id"]

    client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit"],
        "block_on": ["missing_evidence", "failing_required_tests"],
    })

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            status="ready_to_ship",
            base_main_hash="b" * 40,
            composed_commit_hash="c" * 40,
            changed_files=["src/app.py"],
            test_status="passed",
            test_output="1 passed",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    collect_resp = client.post(f"/api/projects/{pid}/evidence/collect", json={
        "target_type": "ship_run",
        "target_id": run_id,
        "check_type": "unit",
    })
    ship_resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert collect_resp.status_code == 201
    bundle = collect_resp.get_json()
    assert bundle["checks"][0]["tool_name"] == "shipper"
    assert bundle["checks"][0]["output"] == "1 passed"
    assert ship_resp.status_code == 200
    assert ship_resp.get_json()["status"] == "shipped"


def test_collect_failed_attempt_validation_evidence(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Bad attempt", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash=None,
            base_hash="b" * 40,
            attempt_num=1,
            status="failed",
            validation_error="No commit hash",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    resp = client.post(f"/api/projects/{pid}/evidence/collect", json={
        "target_type": "attempt",
        "target_id": attempt_id,
    })

    assert resp.status_code == 201
    bundle = resp.get_json()
    assert bundle["status"] == "failed"
    assert bundle["risk_level"] == "high"
    assert bundle["checks"][0]["check_type"] == "validation"
    assert bundle["checks"][0]["status"] == "failed"


def test_run_command_evidence_records_passed_check(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-proj",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    assert resp.status_code == 201
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "print('deterministic ok')"],
        "timeout_seconds": 30,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    assert bundle["status"] == "passed"
    assert bundle["risk_level"] == "low"
    assert bundle["checks"][0]["status"] == "passed"
    assert "deterministic ok" in bundle["checks"][0]["output"]
    assert bundle["checks"][0]["metadata"]["exit_code"] == 0
    assert bundle["checks"][0]["metadata"]["cwd"] == str(project_dir)
    assert bundle["checks"][0]["metadata"]["sandbox"]["enabled"] is True
    assert bundle["checks"][0]["metadata"]["sandbox"]["inherit_env"] is False


def test_run_command_evidence_sandbox_hides_inherited_env(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TERARCHITECT_SECRET_TEST_VALUE", "leaked")
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-sandbox",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Sandbox checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [
            sys.executable,
            "-c",
            "import os; print(os.getenv('TERARCHITECT_SECRET_TEST_VALUE', 'missing'))",
        ],
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert "missing" in check["output"]
    assert "leaked" not in check["output"]
    assert check["metadata"]["sandbox"]["enabled"] is True
    assert check["metadata"]["sandbox"]["env_keys"] == []


def test_run_command_evidence_sandbox_allows_explicit_safe_env(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-sandbox-env",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Sandbox checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "import os; print(os.getenv('SAFE_FLAG'))"],
        "sandbox": {"env": {"SAFE_FLAG": "enabled"}},
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert "enabled" in check["output"]
    assert check["metadata"]["sandbox"]["env_keys"] == ["SAFE_FLAG"]


def test_run_command_evidence_rejects_sensitive_sandbox_env(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-sandbox-reject",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Sandbox checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "print('nope')"],
        "sandbox": {"env": {"API_TOKEN": "secret"}},
    })

    assert run_resp.status_code == 400
    assert "sensitive" in run_resp.get_json()["error"]


def test_run_browser_evidence_records_playwright_artifacts(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-browser",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url="http://127.0.0.1:4173",
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "Path('playwright-report').mkdir(); "
                "Path('playwright-report/index.html').write_text('report'); "
                "Path('test-results').mkdir(); "
                "Path('test-results/trace.zip').write_text('trace'); "
                "print('browser ok')"
            ),
        ],
        "timeout_seconds": 30,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "passed"
    assert check["check_type"] == "e2e"
    assert check["tool_name"] == "playwright"
    assert "browser ok" in check["output"]
    assert check["metadata"]["browser"] is True
    assert check["metadata"]["runner"] == "playwright"
    assert check["metadata"]["preview_url"] == "http://127.0.0.1:4173"
    assert check["metadata"]["preview_status"] == "preview_ready"
    assert check["metadata"]["preview_ready"] is True
    assert check["metadata"]["preview_source"] == "composite_workspace"
    assert check["metadata"]["artifacts"][0]["path"] == "playwright-report/index.html"
    assert check["metadata"]["artifacts"][0]["exists"] is True
    assert check["metadata"]["artifacts"][1]["path"] == "test-results"
    assert check["metadata"]["artifacts"][1]["exists"] is True


def test_run_browser_evidence_records_flake_and_failure_context(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-browser-context",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "Path('playwright-report').mkdir(); Path('playwright-report/index.html').write_text('report'); "
                "Path('test-results').mkdir(); Path('test-results/trace.zip').write_text('trace'); "
                "Path('test-results/failure.png').write_text('png'); "
                "Path('test-results/failure.webm').write_text('video')"
            ),
        ],
        "retry_count": 1,
        "shard": {"index": 1, "total": 3},
        "console_errors": ["TypeError: boom"],
        "network_failures": ["GET /api/widgets 500"],
        "trace_path": "test-results/trace.zip",
        "screenshot_path": "test-results/failure.png",
        "video_path": "test-results/failure.webm",
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["metadata"]["retry_count"] == 1
    assert check["metadata"]["flake"] is True
    assert check["metadata"]["shard"] == {"index": 1, "total": 3}
    assert check["metadata"]["console_errors"] == ["TypeError: boom"]
    assert check["metadata"]["network_failures"] == ["GET /api/widgets 500"]
    artifacts = check["metadata"]["artifacts"]
    assert any(item["kind"] == "trace" and item["path"] == "test-results/trace.zip" for item in artifacts)
    assert any(item["kind"] == "screenshot" and item["exists"] is True for item in artifacts)
    assert any(item["kind"] == "video" and item["exists"] is True for item in artifacts)


def test_run_browser_evidence_fails_when_required_preview_is_not_ready(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-browser-preview-required",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="composing",
            composed_commit_hash="c" * 40,
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [sys.executable, "-c", "print('browser ran')"],
        "preview_required": True,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert check["status"] == "failed"
    assert "Preview environment was required" in check["output"]
    assert check["metadata"]["preview_required"] is True
    assert check["metadata"]["preview_ready"] is False
    assert check["metadata"]["preview_status"] == "composing"


def test_run_browser_evidence_records_managed_preview_command(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-browser-managed-preview",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url="http://127.0.0.1:4173",
            preview_status="ready",
            preview_command=["npm", "run", "preview"],
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [sys.executable, "-c", "print('browser ran')"],
        "preview_launch_required": True,
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["status"] == "passed"
    assert check["metadata"]["preview_command"] == ["npm", "run", "preview"]
    assert check["metadata"]["preview_command_source"] == "composite_workspace"
    assert check["metadata"]["preview_process_status"] == "ready"
    assert check["metadata"]["preview_managed"] is True
    assert check["metadata"]["preview_launch_required"] is True


def test_worker_workspace_composed_records_preview_process_metadata(client, project):
    pid = project["id"]
    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="composing",
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    resp = client.post(f"/api/worker/workspaces/{ws_id}/composed", json={
        "composed_commit_hash": "c" * 40,
        "test_status": "passed",
        "preview_url": "http://127.0.0.1:5173",
        "preview_status": "ready",
        "preview_command": ["npm", "run", "preview"],
    })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "preview_ready"
    assert body["preview_url"] == "http://127.0.0.1:5173"
    assert body["preview_status"] == "ready"
    assert body["preview_command"] == ["npm", "run", "preview"]


def test_run_browser_evidence_auto_detects_preview_command(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / "package.json").write_text(json.dumps({"scripts": {"preview": "vite --host 127.0.0.1"}}))
    resp = client.post("/api/projects", json={
        "name": "local-browser-detect-preview",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url="http://127.0.0.1:4173",
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [sys.executable, "-c", "print('browser ran')"],
        "auto_detect_preview_command": True,
        "preview_launch_required": True,
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["status"] == "passed"
    assert check["metadata"]["preview_command"] == ["npm", "run", "preview"]
    assert check["metadata"]["preview_command_source"] == "package_json"
    assert check["metadata"]["preview_managed"] is True


def test_run_browser_evidence_fails_when_required_preview_command_missing(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-browser-preview-command-required",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url="http://127.0.0.1:4173",
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [sys.executable, "-c", "print('browser ran')"],
        "preview_launch_required": True,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert check["status"] == "failed"
    assert "Preview process command was required" in check["output"]
    assert check["metadata"]["preview_command"] == []
    assert check["metadata"]["preview_managed"] is False


def test_run_browser_evidence_supervises_managed_preview_process(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / "index.html").write_text("preview ok", encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    preview_url = f"http://127.0.0.1:{port}/"

    resp = client.post("/api/projects", json={
        "name": "local-browser-preview-supervised",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url=preview_url,
            preview_status="configured",
            preview_command=[sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [
            sys.executable,
            "-c",
            "import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=3).status)",
            preview_url,
        ],
        "preview_launch_required": True,
        "preview_supervision_enabled": True,
        "preview_ready_timeout_seconds": 5,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "passed"
    assert check["status"] == "passed"
    assert check["metadata"]["preview_supervision_enabled"] is True
    assert check["metadata"]["preview_ready"] is True
    assert check["metadata"]["preview_lifecycle_status"] == "stopped"
    assert check["metadata"]["preview_process_pid"]
    assert check["metadata"]["preview_ready_at"]
    assert check["metadata"]["preview_stopped_at"]


def test_run_browser_evidence_fails_when_supervised_preview_exits_early(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    preview_url = "http://127.0.0.1:9/"
    resp = client.post("/api/projects", json={
        "name": "local-browser-preview-supervised-failure",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url=preview_url,
            preview_command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-browser", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [sys.executable, "-c", "print('browser would otherwise pass')"],
        "preview_launch_required": True,
        "preview_supervision_enabled": True,
        "preview_ready_timeout_seconds": 1,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert check["status"] == "failed"
    assert "Managed preview process did not become ready." in check["output"]
    assert check["metadata"]["preview_ready"] is False
    assert check["metadata"]["preview_process_exit_code"] == 3


def test_async_browser_evidence_run_executes_and_links_bundle(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-browser-async",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url="http://127.0.0.1:4173",
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "browser",
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('playwright-report').mkdir(); Path('playwright-report/index.html').write_text('ok')",
        ],
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    execute_resp = client.post(f"/api/worker/evidence-runs/{run_id}/execute", json={})

    assert execute_resp.status_code == 200
    run = execute_resp.get_json()["run"]
    assert run["status"] == "completed"
    assert run["bundle"]["checks"][0]["metadata"]["browser"] is True
    assert run["bundle"]["checks"][0]["artifact_url"] == "playwright-report/index.html"


def test_run_replay_evidence_records_diff_artifacts(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-replay",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Replay checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('replay-diff.json').write_text('{}'); Path('replay-report.json').write_text('{\"ok\": true}'); print('replay ok')",
        ],
        "traffic_path": "fixtures/traffic.har",
        "contract_path": "openapi.yaml",
        "base_url": "http://stable.test",
        "candidate_url": "http://candidate.test",
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["check_type"] == "replay"
    assert check["tool_name"] == "replay"
    assert "replay ok" in check["output"]
    assert check["metadata"]["replay"] is True
    assert check["metadata"]["traffic_path"] == "fixtures/traffic.har"
    assert check["metadata"]["contract_path"] == "openapi.yaml"
    assert check["metadata"]["base_url"] == "http://stable.test"
    assert check["metadata"]["candidate_url"] == "http://candidate.test"
    assert check["artifact_url"] == "replay-diff.json"
    assert check["metadata"]["artifacts"][0]["kind"] == "diff"
    assert check["metadata"]["artifacts"][0]["exists"] is True
    assert check["metadata"]["artifacts"][1]["path"] == "replay-report.json"


def test_run_replay_evidence_parses_captured_har_and_writes_manifest(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / "fixtures").mkdir()
    (project_dir / "fixtures" / "traffic.har").write_text(json.dumps({
        "log": {
            "entries": [
                {"request": {"method": "GET", "url": "https://stable.test/api/widgets?limit=10"}},
                {"request": {"method": "POST", "url": "https://stable.test/api/widgets"}},
            ]
        }
    }), encoding="utf-8")
    resp = client.post("/api/projects", json={
        "name": "local-replay-har",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Replay checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [sys.executable, "-c", "from pathlib import Path; Path('replay-diff.json').write_text('{}'); print('har replay')"],
        "traffic_path": "fixtures/traffic.har",
        "base_url": "https://stable.test",
        "candidate_url": "https://candidate.test",
        "generate_replay_manifest": True,
        "replay_manifest_path": "tmp/replay-manifest.json",
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "passed"
    assert check["metadata"]["traffic_parsed"] is True
    assert check["metadata"]["traffic_source"] == "har"
    assert check["metadata"]["sample_count"] == 2
    assert check["metadata"]["traffic_endpoints"] == ["GET /api/widgets", "POST /api/widgets"]
    assert check["metadata"]["compared_endpoints"] == ["GET /api/widgets", "POST /api/widgets"]
    assert check["metadata"]["replay_manifest_path"] == "tmp/replay-manifest.json"
    assert check["metadata"]["replay_manifest_written"] is True
    manifest = json.loads((project_dir / "tmp" / "replay-manifest.json").read_text(encoding="utf-8"))
    assert manifest["candidate_url"] == "https://candidate.test"
    assert manifest["entries"][0]["endpoint"] == "GET /api/widgets"


def test_run_replay_evidence_fails_when_required_traffic_parse_fails(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / "traffic.har").write_text("not-json", encoding="utf-8")
    resp = client.post("/api/projects", json={
        "name": "local-replay-har-required",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Replay checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [sys.executable, "-c", "print('runner ok')"],
        "traffic_path": "traffic.har",
        "traffic_parse_required": True,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert check["status"] == "failed"
    assert "Replay traffic parsing was required" in check["output"]
    assert check["metadata"]["traffic_parsed"] is False
    assert "Could not parse traffic file" in check["metadata"]["traffic_parse_error"]


def test_run_replay_evidence_parses_openapi_contract(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / "openapi.json").write_text(json.dumps({
        "openapi": "3.0.0",
        "paths": {
            "/api/widgets": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/api/widgets/{id}": {"patch": {"responses": {"200": {"description": "ok"}}}},
        },
    }))
    resp = client.post("/api/projects", json={
        "name": "local-replay-contract",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Replay checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [sys.executable, "-c", "from pathlib import Path; Path('replay-diff.json').write_text('{}'); print('contract ok')"],
        "contract_path": "openapi.json",
        "contract_validation_required": True,
        "compared_endpoints": ["GET /api/widgets", "PATCH /api/widgets/{id}"],
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "passed"
    assert check["metadata"]["contract_validation_required"] is True
    assert check["metadata"]["contract_parsed"] is True
    assert check["metadata"]["contract_endpoint_count"] == 2
    assert check["metadata"]["contract_missing_endpoints"] == []
    assert check["metadata"]["contract_endpoints"] == ["GET /api/widgets", "PATCH /api/widgets/{id}"]


def test_run_replay_evidence_fails_missing_contract_endpoint(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / "openapi.json").write_text(json.dumps({
        "openapi": "3.0.0",
        "paths": {"/api/widgets": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }))
    resp = client.post("/api/projects", json={
        "name": "local-replay-contract-missing",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Replay checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [sys.executable, "-c", "from pathlib import Path; Path('replay-diff.json').write_text('{}'); print('runner ok')"],
        "contract_path": "openapi.json",
        "compared_endpoints": ["GET /api/widgets", "DELETE /api/widgets/{id}"],
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert bundle["risk_level"] == "high"
    assert check["status"] == "failed"
    assert "Replay regressions detected" in check["output"]
    assert check["metadata"]["contract_parsed"] is True
    assert check["metadata"]["contract_missing_endpoints"] == ["DELETE /api/widgets/{id}"]


def test_run_replay_evidence_derives_candidate_service_from_workspace_preview(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-replay-workspace-preview",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            base_root_hash="b" * 40,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="preview_ready",
            composed_commit_hash="c" * 40,
            preview_url="http://127.0.0.1:4173",
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('replay-diff.json').write_text('{}'); print('workspace replay')",
        ],
        "base_url": "http://stable.test",
        "candidate_url_required": True,
        "base_url_required": True,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["base_hash"] == "b" * 40
    assert bundle["candidate_hash"] == "c" * 40
    assert bundle["status"] == "passed"
    assert check["metadata"]["base_url"] == "http://stable.test"
    assert check["metadata"]["candidate_url"] == "http://127.0.0.1:4173"
    assert check["metadata"]["candidate_url_source"] == "composite_workspace"
    assert check["metadata"]["candidate_service_status"] == "preview_ready"
    assert check["metadata"]["candidate_service_ready"] is True
    assert check["metadata"]["service_comparison_ready"] is True
    assert check["metadata"]["target_base_hash"] == "b" * 40
    assert check["metadata"]["target_candidate_hash"] == "c" * 40


def test_run_replay_evidence_fails_when_required_candidate_service_is_not_ready(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-replay-candidate-required",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, CompositeWorkspace
    with client.application.app_context():
        ws = CompositeWorkspace(
            project_id=pid,
            base_root_hash="b" * 40,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="composing",
            composed_commit_hash="c" * 40,
        )
        db.session.add(ws)
        db.session.commit()
        ws_id = str(ws.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "composite_workspace",
        "target_id": ws_id,
        "command": [sys.executable, "-c", "print('replay runner')"],
        "base_url": "http://stable.test",
        "candidate_url_required": True,
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert bundle["risk_level"] == "high"
    assert check["status"] == "failed"
    assert "Replay candidate service was required but not ready" in check["output"]
    assert check["metadata"]["candidate_url_required"] is True
    assert check["metadata"]["candidate_service_ready"] is False
    assert check["metadata"]["candidate_service_status"] == "composing"
    assert check["metadata"]["service_comparison_ready"] is False


def test_run_replay_evidence_fails_structured_regressions(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-replay-regression",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Replay checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-replay", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [sys.executable, "-c", "from pathlib import Path; Path('replay-diff.json').write_text('{}'); print('runner ok')"],
        "traffic_source": "staging-har",
        "sample_count": 25,
        "contract_compatible": False,
        "compared_endpoints": ["GET /api/widgets"],
        "status_code_regressions": ["GET /api/widgets 200 -> 500"],
        "schema_regressions": ["GET /api/widgets missing id"],
        "auth_regressions": ["GET /api/admin allowed anonymous"],
        "behavior_regressions": ["GET /api/widgets sorting changed"],
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert bundle["risk_level"] == "high"
    assert check["status"] == "failed"
    assert "Replay regressions detected" in check["output"]
    assert check["metadata"]["traffic_source"] == "staging-har"
    assert check["metadata"]["sample_count"] == 25
    assert check["metadata"]["contract_compatible"] is False
    assert check["metadata"]["compared_endpoints"] == ["GET /api/widgets"]
    assert check["metadata"]["regression_detected"] is True
    assert check["metadata"]["regression_counts"] == {
        "status_code": 1,
        "schema": 1,
        "auth": 1,
        "behavior": 1,
    }


def test_async_replay_evidence_run_executes_and_links_bundle(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-replay-async",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Replay checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "replay",
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('replay-diff.json').write_text('{}'); print('queued replay')",
        ],
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    execute_resp = client.post(f"/api/worker/evidence-runs/{run_id}/execute", json={})

    assert execute_resp.status_code == 200
    run = execute_resp.get_json()["run"]
    assert run["status"] == "completed"
    assert run["bundle"]["checks"][0]["metadata"]["replay"] is True
    assert run["bundle"]["checks"][0]["artifact_url"] == "replay-diff.json"


def test_run_llm_review_evidence_records_structured_findings(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-llm-review",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="LLM checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    findings = {
        "findings": [{
            "severity": "high",
            "path": "backend/api/routes.py",
            "line": 42,
            "claim": "Endpoint exposes sensitive data without authorization.",
            "evidence": "The route returns secrets to any caller.",
            "suggested_fix": "Require project-scoped authorization before returning data.",
            "blocking": True,
            "confidence": 0.91,
        }]
    }
    run_resp = client.post(f"/api/projects/{pid}/evidence/run-llm-review", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "reviewer": "security_reviewer",
        "model": "review-model",
        "prompt_version": "security-v1",
        "command": [
            sys.executable,
            "-c",
            f"import json; print(json.dumps({findings!r}))",
        ],
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["check_type"] == "llm_review"
    assert check["status"] == "failed"
    assert check["tool_name"] == "security_reviewer"
    assert check["metadata"]["llm_review"] is True
    assert check["metadata"]["reviewer"] == "security_reviewer"
    assert check["metadata"]["model"] == "review-model"
    assert check["metadata"]["prompt_version"] == "security-v1"
    assert check["metadata"]["blocking_findings"] == 1
    assert check["metadata"]["findings"][0]["claim"].startswith("Endpoint exposes")
    assert check["artifact_url"] == "llm-review.json"


def test_async_llm_review_evidence_run_executes_and_links_bundle(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-llm-review-async",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="LLM checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "llm_review",
        "target_type": "attempt",
        "target_id": attempt_id,
        "reviewer": "architecture_reviewer",
        "command": [
            sys.executable,
            "-c",
            "print('{\"findings\": []}')",
        ],
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    execute_resp = client.post(f"/api/worker/evidence-runs/{run_id}/execute", json={})

    assert execute_resp.status_code == 200
    run = execute_resp.get_json()["run"]
    assert run["status"] == "completed"
    check = run["bundle"]["checks"][0]
    assert check["status"] == "passed"
    assert check["metadata"]["llm_review"] is True
    assert check["metadata"]["reviewer"] == "architecture_reviewer"
    assert check["metadata"]["findings"] == []


def test_run_llm_review_evidence_records_malformed_output_as_failure(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-llm-review-malformed",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="LLM checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-llm-review", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "reviewer": "security_reviewer",
        "command": [sys.executable, "-c", "print('not-json')"],
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["status"] == "failed"
    assert check["metadata"]["findings"] == []
    assert "structured JSON findings" in check["metadata"]["parse_error"]


def test_run_test_adequacy_evidence_records_blockers(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-test-adequacy",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Adequacy checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-test-adequacy", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "generated_test_paths": ["backend/tests/test_auth.py"],
        "acceptance_criteria": ["Reject unauthenticated access"],
        "findings": [{
            "criterion": "Reject unauthenticated access",
            "test_path": "backend/tests/test_auth.py",
            "covered": False,
            "severity": "high",
            "claim": "Generated tests only assert the happy path.",
            "evidence": "No unauthorized request is exercised.",
            "suggested_fix": "Add a failing auth-path assertion.",
        }],
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "failed"
    assert check["check_type"] == "test_adequacy"
    assert check["status"] == "failed"
    assert check["metadata"]["test_adequacy"] is True
    assert check["metadata"]["generated_test_paths"] == ["backend/tests/test_auth.py"]
    assert check["metadata"]["acceptance_criteria"] == ["Reject unauthenticated access"]
    assert check["metadata"]["blocking_findings"] == 1
    assert check["metadata"]["uncovered_criteria"] == ["Reject unauthenticated access"]
    assert check["artifact_url"] == "test-adequacy.json"


def test_run_test_adequacy_evidence_generates_candidate_tests_from_ticket_criteria(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-test-generation",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Password reset",
            intent_status="active",
            acceptance_criteria="- Reject expired reset tokens\n- Email a reset confirmation",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-test-adequacy", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "generate_candidate_tests": True,
        "generated_test_prefix": "backend/tests/generated",
        "generated_test_framework": "pytest",
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["status"] == "passed"
    assert check["metadata"]["acceptance_criteria"] == [
        "Reject expired reset tokens",
        "Email a reset confirmation",
    ]
    candidates = check["metadata"]["generated_test_candidates"]
    assert check["metadata"]["generated_test_candidate_count"] == 2
    assert candidates[0]["criterion"] == "Reject expired reset tokens"
    assert candidates[0]["suggested_path"] == "backend/tests/generated/test_reject_expired_reset_tokens.py"
    assert candidates[0]["test_name"] == "test_reject_expired_reset_tokens"
    assert "pytest" in candidates[0]["prompt"]


def test_run_test_adequacy_evidence_writes_generated_test_files(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-test-generation-write",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Invite flow",
            intent_status="active",
            acceptance_criteria="- Reject duplicate invite emails",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    generated_path = "backend/tests/generated/test_reject_duplicate_invite_emails.py"
    body = (
        "def test_reject_duplicate_invite_emails():\n"
        "    assert 'duplicate invite' == 'duplicate invite'\n"
    )
    run_resp = client.post(f"/api/projects/{pid}/evidence/run-test-adequacy", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "generate_candidate_tests": True,
        "write_generated_tests": True,
        "generated_test_prefix": "backend/tests/generated",
        "generated_test_bodies": {generated_path: body},
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert (project_dir / generated_path).read_text(encoding="utf-8") == body
    assert check["metadata"]["generated_test_paths"] == [generated_path]
    assert check["metadata"]["generated_test_file_count"] == 1
    assert check["metadata"]["generated_test_files_written"][0]["path"] == generated_path


def test_run_test_adequacy_evidence_rejects_generated_test_path_escape(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-test-generation-escape",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Invite flow",
            intent_status="active",
            acceptance_criteria="- Reject duplicate invite emails",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-test-adequacy", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "generate_candidate_tests": True,
        "write_generated_tests": True,
        "generated_test_prefix": "../outside",
    })

    assert run_resp.status_code == 400
    assert "generated test path" in run_resp.get_json()["error"]


def test_async_test_adequacy_evidence_run_executes_and_links_bundle(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-test-adequacy-async",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Adequacy checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "test_adequacy",
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [
            sys.executable,
            "-c",
            "print('{\"findings\": []}')",
        ],
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    execute_resp = client.post(f"/api/worker/evidence-runs/{run_id}/execute", json={})

    assert execute_resp.status_code == 200
    run = execute_resp.get_json()["run"]
    assert run["status"] == "completed"
    check = run["bundle"]["checks"][0]
    assert check["check_type"] == "test_adequacy"
    assert check["status"] == "passed"
    assert check["metadata"]["test_adequacy"] is True
    assert check["metadata"]["findings"] == []


def test_async_test_adequacy_generation_preserves_candidate_metadata(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-test-generation-async",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Invite flow",
            intent_status="active",
            acceptance_criteria="Reject duplicate invite emails",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "test_adequacy",
        "target_type": "attempt",
        "target_id": attempt_id,
        "generate_candidate_tests": True,
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    execute_resp = client.post(f"/api/worker/evidence-runs/{run_id}/execute", json={})

    assert execute_resp.status_code == 200
    check = execute_resp.get_json()["run"]["bundle"]["checks"][0]
    assert check["metadata"]["acceptance_criteria"] == ["Reject duplicate invite emails"]
    assert check["metadata"]["generated_test_candidates"][0]["test_name"] == "test_reject_duplicate_invite_emails"


def test_run_mutation_evidence_records_report_artifacts(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-mutation",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Mutation checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-mutation", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('mutation-report.json').write_text('{\"score\": 92}'); print('mutation ok')",
        ],
        "changed_paths": ["backend/api/services/evidence_service.py"],
        "mutation_threshold": 85,
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["check_type"] == "mutation"
    assert check["tool_name"] == "mutation"
    assert check["metadata"]["mutation"] is True
    assert check["metadata"]["changed_paths"] == ["backend/api/services/evidence_service.py"]
    assert check["metadata"]["mutation_threshold"] == 85.0
    assert check["artifact_url"] == "mutation-report.json"
    assert check["metadata"]["artifacts"][0]["exists"] is True


def test_async_property_evidence_run_executes_and_links_bundle(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-property-async",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Property checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "property",
        "target_type": "attempt",
        "target_id": attempt_id,
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('property-report.json').write_text('{\"examples\": 50}'); print('property ok')",
        ],
        "properties": ["roundtrip_serialization"],
        "generated_cases": 50,
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    execute_resp = client.post(f"/api/worker/evidence-runs/{run_id}/execute", json={})

    assert execute_resp.status_code == 200
    run = execute_resp.get_json()["run"]
    assert run["status"] == "completed"
    check = run["bundle"]["checks"][0]
    assert check["check_type"] == "property"
    assert check["metadata"]["property"] is True
    assert check["metadata"]["properties"] == ["roundtrip_serialization"]
    assert check["metadata"]["generated_cases"] == 50
    assert check["artifact_url"] == "property-report.json"


def test_run_command_evidence_records_project_artifacts(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / "reports").mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-artifacts",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    assert resp.status_code == 201
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Artifact checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('reports/unit.txt').write_text('ok')",
        ],
        "artifacts": [
            {"kind": "report", "label": "unit report", "path": "reports/unit.txt"},
            {"kind": "log", "label": "ci log", "url": "https://example.test/unit.log"},
        ],
    })

    assert run_resp.status_code == 201
    check = run_resp.get_json()["checks"][0]
    assert check["artifact_url"] == "reports/unit.txt"
    assert check["metadata"]["artifacts"][0] == {
        "kind": "report",
        "label": "unit report",
        "path": "reports/unit.txt",
        "exists": True,
    }
    assert check["metadata"]["artifacts"][1]["url"] == "https://example.test/unit.log"


def test_run_command_evidence_rejects_artifacts_outside_project(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-artifact-reject",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Artifact checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "print('nope')"],
        "artifacts": [{"kind": "report", "path": "../outside.txt"}],
    })

    assert run_resp.status_code == 400
    assert "artifact path" in run_resp.get_json()["error"]


def test_async_evidence_run_claim_and_execute_command(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-async-evidence",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    assert resp.status_code == 201
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Async checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "command",
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "print('async ok')"],
    })
    assert queue_resp.status_code == 202
    queued = queue_resp.get_json()
    assert queued["status"] == "queued"
    assert queued["evidence_bundle_id"] is None

    assert client.get(f"/api/projects/{pid}/evidence").get_json() == []

    claim_resp = client.post("/api/worker/evidence-runs/next", json={"project_id": pid})
    assert claim_resp.status_code == 200
    claimed = claim_resp.get_json()["run"]
    assert claimed["id"] == queued["id"]
    assert claimed["status"] == "running"

    execute_resp = client.post(f"/api/worker/evidence-runs/{claimed['id']}/execute", json={})
    assert execute_resp.status_code == 200
    completed = execute_resp.get_json()["run"]
    assert completed["status"] == "completed"
    assert completed["evidence_bundle_id"]
    assert completed["bundle"]["status"] == "passed"
    assert completed["bundle"]["checks"][0]["status"] == "passed"
    assert "async ok" in completed["bundle"]["checks"][0]["output"]


def test_async_evidence_run_records_execution_failure(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-async-failure",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Async checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "command",
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "print('will not run')"],
        "cwd": "missing",
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    execute_resp = client.post(f"/api/worker/evidence-runs/{run_id}/execute", json={})

    assert execute_resp.status_code == 200
    run = execute_resp.get_json()["run"]
    assert run["status"] == "failed"
    assert "cwd" in run["error"]
    assert run["evidence_bundle_id"] is None


def test_external_worker_can_complete_non_local_evidence_run(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="External checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "command",
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "integration",
        "command": "pytest tests/integration",
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    claim_resp = client.post("/api/worker/evidence-runs/next", json={"project_id": pid})
    assert claim_resp.status_code == 200
    assert claim_resp.get_json()["run"]["status"] == "running"

    complete_resp = client.post(f"/api/worker/evidence-runs/{run_id}/complete", json={
        "worker_id": "remote-worker-1",
        "summary": "Remote integration evidence passed",
        "checks": [{
            "check_type": "integration",
            "status": "passed",
            "tool_name": "remote_pytest",
            "command": "pytest tests/integration",
            "output": "12 passed",
            "metadata": {"runtime": "remote"},
            "artifacts": [{
                "kind": "report",
                "label": "CI report",
                "url": "https://ci.example.test/reports/1",
            }],
        }],
    })

    assert complete_resp.status_code == 200
    run = complete_resp.get_json()["run"]
    assert run["status"] == "completed"
    assert run["evidence_bundle_id"]
    check = run["bundle"]["checks"][0]
    assert run["bundle"]["status"] == "passed"
    assert check["status"] == "passed"
    assert check["metadata"]["external_worker"] is True
    assert check["metadata"]["worker_id"] == "remote-worker-1"
    assert check["metadata"]["runtime"] == "remote"
    assert check["artifact_url"] == "https://ci.example.test/reports/1"


def test_external_worker_can_complete_queued_llm_review_with_findings(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Remote LLM checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash="d" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "llm_review",
        "target_type": "attempt",
        "target_id": attempt_id,
        "reviewer": "security_reviewer",
        "model": "gpt-review",
        "prompt_version": "security-v2",
        "external_worker": True,
    })
    assert queue_resp.status_code == 202
    queued = queue_resp.get_json()
    assert queued["request_data"]["external_worker_required"] is True
    run_id = queued["id"]

    complete_resp = client.post(f"/api/worker/evidence-runs/{run_id}/complete", json={
        "worker_id": "llm-worker-1",
        "summary": "Remote security review found a blocker",
        "findings": [{
            "severity": "critical",
            "path": "backend/api/routes.py",
            "line": 77,
            "claim": "Unauthenticated callers can mutate project state.",
            "evidence": "The worker route does not verify project access.",
            "suggested_fix": "Require worker authentication before mutation.",
            "blocking": True,
            "confidence": 0.94,
        }],
    })

    assert complete_resp.status_code == 200
    run = complete_resp.get_json()["run"]
    assert run["status"] == "completed"
    assert run["bundle"]["status"] == "failed"
    assert run["bundle"]["risk_level"] == "high"
    check = run["bundle"]["checks"][0]
    assert check["check_type"] == "llm_review"
    assert check["status"] == "failed"
    assert check["tool_name"] == "security_reviewer"
    assert check["metadata"]["external_worker"] is True
    assert check["metadata"]["worker_id"] == "llm-worker-1"
    assert check["metadata"]["llm_review"] is True
    assert check["metadata"]["reviewer"] == "security_reviewer"
    assert check["metadata"]["model"] == "gpt-review"
    assert check["metadata"]["prompt_version"] == "security-v2"
    assert check["metadata"]["blocking_findings"] == 1
    assert check["metadata"]["finding_counts"]["critical"] == 1
    assert check["metadata"]["findings"][0]["claim"].startswith("Unauthenticated")
    assert check["artifact_url"] == "llm-review.json"


def test_external_worker_can_fail_evidence_run_without_bundle(client, project):
    pid = project["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="External checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    queue_resp = client.post(f"/api/projects/{pid}/evidence/runs", json={
        "run_type": "llm_review",
        "target_type": "attempt",
        "target_id": attempt_id,
        "reviewer": "security_reviewer",
        "findings": [],
    })
    assert queue_resp.status_code == 202
    run_id = queue_resp.get_json()["id"]

    fail_resp = client.post(f"/api/worker/evidence-runs/{run_id}/fail", json={
        "error": "remote worker could not reach model provider",
    })

    assert fail_resp.status_code == 200
    run = fail_resp.get_json()["run"]
    assert run["status"] == "failed"
    assert run["evidence_bundle_id"] is None
    assert "model provider" in run["error"]


def test_run_command_evidence_records_failed_check(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-proj",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(project_id=pid, status="ready_to_ship", composed_commit_hash="c" * 40)
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "ship_run",
        "target_id": run_id,
        "check_type": "integration",
        "command": [sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"],
    })

    assert run_resp.status_code == 201
    bundle = run_resp.get_json()
    assert bundle["status"] == "failed"
    assert bundle["risk_level"] == "high"
    assert bundle["checks"][0]["status"] == "failed"
    assert bundle["checks"][0]["metadata"]["exit_code"] == 7
    assert "bad" in bundle["checks"][0]["output"]


def test_rerun_failed_evidence_checks_records_new_bundle(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-rerun",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Rerun checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    command = [
        sys.executable,
        "-c",
        "import pathlib, sys; sys.exit(0 if pathlib.Path('ok').exists() else 7)",
    ]
    failed_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": command,
    })
    assert failed_resp.status_code == 201
    failed_bundle = failed_resp.get_json()
    failed_check_id = failed_bundle["checks"][0]["id"]
    assert failed_bundle["status"] == "failed"

    (project_dir / "ok").write_text("fixed\n", encoding="utf-8")
    rerun_resp = client.post(f"/api/projects/{pid}/evidence/{failed_bundle['id']}/rerun", json={})

    assert rerun_resp.status_code == 201
    rerun_bundle = rerun_resp.get_json()
    rerun_check = rerun_bundle["checks"][0]
    assert rerun_bundle["id"] != failed_bundle["id"]
    assert rerun_bundle["status"] == "passed"
    assert rerun_check["status"] == "passed"
    assert rerun_check["metadata"]["rerun"] is True
    assert rerun_check["metadata"]["rerun_of_bundle_id"] == failed_bundle["id"]
    assert rerun_check["metadata"]["rerun_of_check_id"] == failed_check_id


def test_rerun_failed_evidence_checks_rejects_non_command_failures(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-rerun-reject",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]
    bundle = client.post(f"/api/projects/{pid}/evidence", json={
        "target_type": "attempt",
        "target_id": "cdcdcdcd-0000-0000-0000-000000000013",
        "status": "failed",
        "risk_level": "high",
    }).get_json()
    assert client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/checks", json={
        "check_type": "validation",
        "status": "failed",
        "output": "No commit hash",
    }).status_code == 201

    rerun_resp = client.post(f"/api/projects/{pid}/evidence/{bundle['id']}/rerun", json={})

    assert rerun_resp.status_code == 400
    assert "No failed command-backed" in rerun_resp.get_json()["error"]


def test_run_configured_check_suite_records_multiple_checks(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-suite",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    assert resp.status_code == 201
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Suite checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    policy_resp = client.put(f"/api/projects/{pid}/verification-policy", json={
        "required_checks": ["unit"],
        "check_suites": [
            {
                "check_type": "unit",
                "command": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('suite-report.txt').write_text('ok'); print('ok')",
                ],
                "artifacts": [{"kind": "report", "path": "suite-report.txt"}],
            },
            {"check_type": "static", "command": [sys.executable, "-c", "import sys; print('bad'); sys.exit(4)"]},
        ],
    })
    assert policy_resp.status_code == 200

    suite_resp = client.post(f"/api/projects/{pid}/evidence/run-suite", json={
        "target_type": "attempt",
        "target_id": attempt_id,
    })

    assert suite_resp.status_code == 201
    bundle = suite_resp.get_json()
    assert bundle["status"] == "failed"
    assert len(bundle["checks"]) == 2
    assert [check["check_type"] for check in bundle["checks"]] == ["unit", "static"]
    assert bundle["checks"][0]["status"] == "passed"
    assert bundle["checks"][1]["status"] == "failed"
    assert bundle["checks"][0]["metadata"]["suite"] is True
    assert bundle["checks"][0]["artifact_url"] == "suite-report.txt"
    assert bundle["checks"][0]["metadata"]["artifacts"][0]["exists"] is True
    assert bundle["checks"][1]["metadata"]["exit_code"] == 4


def test_run_check_suite_rejects_missing_configuration(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-suite-empty",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(project_id=pid, status="ready_to_ship", composed_commit_hash="c" * 40)
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    suite_resp = client.post(f"/api/projects/{pid}/evidence/run-suite", json={
        "target_type": "ship_run",
        "target_id": run_id,
    })

    assert suite_resp.status_code == 400
    assert "No check_suites" in suite_resp.get_json()["error"]


def test_compare_candidate_evidence_records_changed_files(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=project_dir, check=True)
    (project_dir / "app.py").write_text("print('base')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=project_dir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=project_dir, check=True, capture_output=True)
    base_hash = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (project_dir / "app.py").write_text("print('candidate')\nprint('new')\n", encoding="utf-8")
    (project_dir / "tests.py").write_text("assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py", "tests.py"], cwd=project_dir, check=True)
    subprocess.run(["git", "commit", "-m", "candidate"], cwd=project_dir, check=True, capture_output=True)
    candidate_hash = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    resp = client.post("/api/projects", json={
        "name": "local-compare",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    assert resp.status_code == 201
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Compared", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash=candidate_hash,
            base_hash=base_hash,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    compare_resp = client.post(f"/api/projects/{pid}/evidence/compare", json={
        "target_type": "attempt",
        "target_id": attempt_id,
    })

    assert compare_resp.status_code == 201
    bundle = compare_resp.get_json()
    check = bundle["checks"][0]
    assert bundle["base_hash"] == base_hash
    assert bundle["candidate_hash"] == candidate_hash
    assert bundle["status"] == "passed"
    assert check["check_type"] == "diff"
    assert check["status"] == "passed"
    assert check["metadata"]["changed_file_count"] == 2
    assert check["metadata"]["changed_files"] == ["app.py", "tests.py"]
    assert check["metadata"]["line_counts"]["app.py"]["additions"] == 2
    assert check["metadata"]["line_counts"]["app.py"]["deletions"] == 1
    assert "files changed" in check["output"]


def test_compare_candidate_evidence_rejects_missing_hashes(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-compare-missing",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(project_id=pid, status="ready_to_ship")
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    compare_resp = client.post(f"/api/projects/{pid}/evidence/compare", json={
        "target_type": "ship_run",
        "target_id": run_id,
    })

    assert compare_resp.status_code == 400
    assert "base_hash" in compare_resp.get_json()["error"]


def test_run_command_evidence_rejects_non_local_project(client, project, accepted_ticket_and_attempt):
    pid = project["id"]
    _, attempt_id = accepted_ticket_and_attempt

    resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "print('nope')"],
    })

    assert resp.status_code == 400
    assert "local project" in resp.get_json()["error"]


def test_run_command_evidence_rejects_cwd_outside_project(client, tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    resp = client.post("/api/projects", json={
        "name": "local-proj",
        "execution_mode": "local",
        "project_path": str(project_dir),
        "is_existing_repo": True,
    })
    pid = resp.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Checked", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(project_id=pid, ticket_id=ticket.id, attempt_num=1, status="accepted")
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    run_resp = client.post(f"/api/projects/{pid}/evidence/run-command", json={
        "target_type": "attempt",
        "target_id": attempt_id,
        "check_type": "unit",
        "command": [sys.executable, "-c", "print('nope')"],
        "cwd": "..",
    })

    assert run_resp.status_code == 400
    assert "inside project_path" in run_resp.get_json()["error"]
