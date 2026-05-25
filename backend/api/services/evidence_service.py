"""Evidence bundle serialization and status helpers (Phase 14)."""
from collections import Counter
from datetime import datetime, timezone
import json
import os
import shlex
import subprocess
import tempfile
import time
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from models.db import db, Project, EvidenceBundle, EvidenceCheck, EvidenceRun, CompositeWorkspace, ShipRun, Ticket, TicketAttempt, AgentJob
from .channel_service import (
    event_content as _event_content,
    post_event as _post_event,
    project_channel as _project_channel,
    ticket_channel as _ticket_channel,
    wave_channel as _wave_channel,
)


VALID_TARGET_TYPES = {"attempt", "ship_run", "composite_workspace", "snapshot"}
VALID_BUNDLE_STATUSES = {"collecting", "passed", "failed", "warning", "incomplete"}
VALID_RISK_LEVELS = {"low", "medium", "high", "unknown"}
VALID_CHECK_STATUSES = {"passed", "failed", "warning", "skipped"}
VALID_ARTIFACT_KINDS = {"log", "report", "trace", "screenshot", "video", "diff", "coverage", "other"}
VALID_EVIDENCE_RUN_TYPES = {
    "command",
    "suite",
    "browser",
    "replay",
    "llm_review",
    "test_adequacy",
    "mutation",
    "property",
}
VALID_EVIDENCE_RUN_STATUSES = {"queued", "running", "completed", "failed", "canceled"}
DEFAULT_VERIFICATION_POLICY = {
    "required_checks": [],
    "optional_checks": ["integration", "e2e", "static", "security", "visual"],
    "required_llm_reviewers": [],
    "block_on": [],
    "check_suites": [],
}


def _parse_iso_datetime(value: Any):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def evidence_bundle_to_json(bundle: EvidenceBundle, *, include_checks: bool = False) -> dict:
    checks = list(bundle.checks or [])
    check_counts = Counter(c.status for c in checks)
    out = {
        "id": str(bundle.id),
        "project_id": str(bundle.project_id),
        "target_type": bundle.target_type,
        "target_id": str(bundle.target_id),
        "base_hash": bundle.base_hash,
        "candidate_hash": bundle.candidate_hash,
        "selected_attempt_ids": bundle.selected_attempt_ids or [],
        "selected_leaf_hashes": bundle.selected_leaf_hashes or [],
        "status": bundle.status,
        "risk_level": bundle.risk_level,
        "summary": bundle.summary,
        "check_counts": dict(check_counts),
        "created_at": bundle.created_at.isoformat() if bundle.created_at else None,
        "updated_at": bundle.updated_at.isoformat() if bundle.updated_at else None,
    }
    if include_checks:
        out["checks"] = [evidence_check_to_json(c) for c in checks]
    return out


def evidence_check_to_json(check: EvidenceCheck) -> dict:
    return {
        "id": str(check.id),
        "evidence_bundle_id": str(check.evidence_bundle_id),
        "check_type": check.check_type,
        "status": check.status,
        "tool_name": check.tool_name,
        "command": check.command,
        "output": check.output,
        "artifact_url": check.artifact_url,
        "metadata": check.check_metadata or {},
        "started_at": check.started_at.isoformat() if check.started_at else None,
        "finished_at": check.finished_at.isoformat() if check.finished_at else None,
        "created_at": check.created_at.isoformat() if check.created_at else None,
        "updated_at": check.updated_at.isoformat() if check.updated_at else None,
    }


def evidence_run_to_json(run: EvidenceRun, *, include_bundle: bool = False) -> dict:
    out = {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "evidence_bundle_id": str(run.evidence_bundle_id) if run.evidence_bundle_id else None,
        "run_type": run.run_type,
        "status": run.status,
        "target_type": run.target_type,
        "target_id": str(run.target_id),
        "check_type": run.check_type,
        "request_data": run.request_data or {},
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }
    if include_bundle and run.bundle:
        out["bundle"] = evidence_bundle_to_json(run.bundle, include_checks=True)
    return out


def normalize_verification_policy(data: dict | None) -> dict:
    data = data or {}
    policy = dict(DEFAULT_VERIFICATION_POLICY)
    for key in ("required_checks", "optional_checks", "required_llm_reviewers", "block_on"):
        if key in data:
            value = data.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{key} must be a list of strings")
            policy[key] = [item.strip() for item in value if item.strip()]
    if "check_suites" in data:
        value = data.get("check_suites")
        if not isinstance(value, list):
            raise ValueError("check_suites must be a list")
        policy["check_suites"] = [_normalize_check_suite_item(item) for item in value]
    return policy


def evaluate_evidence_policy(project, target_type: str, target_id: str) -> dict:
    policy = normalize_verification_policy(getattr(project, "verification_policy", None))
    bundle = (
        EvidenceBundle.query
        .filter_by(project_id=project.id, target_type=target_type, target_id=target_id)
        .order_by(EvidenceBundle.created_at.desc())
        .first()
    )
    reasons = []
    required = {}
    required_llm_reviewers = {}
    human_approval = None

    if not bundle:
        if "missing_evidence" in policy["block_on"]:
            reasons.append("No evidence bundle exists for this target.")
        if "missing_human_approval" in policy["block_on"]:
            reasons.append("Human approval referencing this evidence bundle is required.")
        for check_type in policy["required_checks"]:
            required[check_type] = {"status": "missing", "passed": False}
        for reviewer in policy["required_llm_reviewers"]:
            required_llm_reviewers[reviewer] = {"status": "missing", "passed": False}
        return {
            "allowed": not reasons,
            "target_type": target_type,
            "target_id": target_id,
            "policy": policy,
            "bundle": None,
            "required_checks": required,
            "required_llm_reviewers": required_llm_reviewers,
            "human_approval": None,
            "reasons": reasons,
        }

    all_checks = list(bundle.checks or [])
    checks_by_type: dict[str, list[EvidenceCheck]] = {}
    for check in all_checks:
        checks_by_type.setdefault(check.check_type, []).append(check)

    for check_type in policy["required_checks"]:
        checks = checks_by_type.get(check_type) or []
        passed = any(c.status == "passed" for c in checks)
        waiver = _latest_waiver(checks)
        failed = any(c.status == "failed" for c in checks)
        status = "passed" if passed else "waived" if waiver else "failed" if failed else "missing"
        required[check_type] = {
            "status": status,
            "passed": passed or bool(waiver),
            **({"waiver": evidence_check_to_json(waiver)} if waiver else {}),
        }
        if not passed and not waiver and "failing_required_tests" in policy["block_on"]:
            if failed:
                reasons.append(f"Required evidence check '{check_type}' failed.")
            else:
                reasons.append(f"Required evidence check '{check_type}' is missing.")

    llm_checks = checks_by_type.get("llm_review") or []
    for reviewer in policy["required_llm_reviewers"]:
        reviewer_checks = [
            check for check in llm_checks
            if (check.check_metadata or {}).get("reviewer") == reviewer
            or check.tool_name == reviewer
        ]
        passed = any(c.status == "passed" for c in reviewer_checks)
        failed = any(c.status == "failed" for c in reviewer_checks)
        warning = any(c.status == "warning" for c in reviewer_checks)
        latest = reviewer_checks[-1] if reviewer_checks else None
        status = "passed" if passed else "failed" if failed else "warning" if warning else "missing"
        required_llm_reviewers[reviewer] = {
            "status": status,
            "passed": passed,
            **({"check": evidence_check_to_json(latest)} if latest else {}),
        }
        if not passed:
            if status == "missing" and "missing_evidence" in policy["block_on"]:
                reasons.append(f"Required LLM reviewer '{reviewer}' is missing.")
            elif status in {"failed", "warning"} and "failing_required_tests" in policy["block_on"]:
                reasons.append(f"Required LLM reviewer '{reviewer}' produced {status} evidence.")

    unwaived_failed_required = any(
        check.get("status") in {"failed", "missing"} for check in required.values()
    )
    if bundle.status in {"failed", "incomplete"} and (
        not policy["required_checks"] or unwaived_failed_required
    ):
        reasons.append(f"Evidence bundle status is {bundle.status}.")

    if "missing_human_approval" in policy["block_on"]:
        human_approval = _latest_human_approval(all_checks)
        if not human_approval:
            reasons.append("Human approval referencing this evidence bundle is required.")

    return {
        "allowed": not reasons,
        "target_type": target_type,
        "target_id": target_id,
        "policy": policy,
        "bundle": evidence_bundle_to_json(bundle, include_checks=True),
        "required_checks": required,
        "required_llm_reviewers": required_llm_reviewers,
        "human_approval": evidence_check_to_json(human_approval) if human_approval else None,
        "reasons": reasons,
    }


def create_evidence_bundle(project_id, data: dict) -> EvidenceBundle:
    target_type = (data.get("target_type") or "").strip()
    if target_type not in VALID_TARGET_TYPES:
        raise ValueError("target_type must be one of: " + ", ".join(sorted(VALID_TARGET_TYPES)))
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    status = (data.get("status") or "collecting").strip()
    if status not in VALID_BUNDLE_STATUSES:
        raise ValueError("status must be one of: " + ", ".join(sorted(VALID_BUNDLE_STATUSES)))
    risk_level = (data.get("risk_level") or "unknown").strip()
    if risk_level not in VALID_RISK_LEVELS:
        raise ValueError("risk_level must be one of: " + ", ".join(sorted(VALID_RISK_LEVELS)))

    bundle = EvidenceBundle(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        base_hash=(data.get("base_hash") or "").strip() or None,
        candidate_hash=(data.get("candidate_hash") or "").strip() or None,
        selected_attempt_ids=data.get("selected_attempt_ids") or [],
        selected_leaf_hashes=data.get("selected_leaf_hashes") or [],
        status=status,
        risk_level=risk_level,
        summary=(data.get("summary") or "").strip() or None,
    )
    db.session.add(bundle)
    db.session.commit()
    return bundle


def add_evidence_check(bundle: EvidenceBundle, data: dict) -> EvidenceCheck:
    check_type = (data.get("check_type") or "").strip()
    if not check_type:
        raise ValueError("check_type is required")
    status = (data.get("status") or "skipped").strip()
    if status not in VALID_CHECK_STATUSES:
        raise ValueError("status must be one of: " + ", ".join(sorted(VALID_CHECK_STATUSES)))

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    artifacts = _normalize_artifacts(data.get("artifacts") or metadata.get("artifacts"))
    if artifacts:
        metadata = {**metadata, "artifacts": artifacts}

    check = EvidenceCheck(
        evidence_bundle_id=bundle.id,
        check_type=check_type,
        status=status,
        tool_name=(data.get("tool_name") or "").strip() or None,
        command=(data.get("command") or "").strip() or None,
        output=(data.get("output") or "")[:12000] or None,
        artifact_url=_primary_artifact_ref(artifacts, data.get("artifact_url")),
        check_metadata=metadata,
        started_at=_parse_iso_datetime(data.get("started_at")),
        finished_at=_parse_iso_datetime(data.get("finished_at")) or datetime.now(timezone.utc),
    )
    db.session.add(check)
    db.session.commit()
    return check


def add_evidence_waiver(bundle: EvidenceBundle, data: dict) -> EvidenceCheck:
    check_type = (data.get("check_type") or "").strip()
    if not check_type:
        raise ValueError("check_type is required")
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise ValueError("reason is required")
    actor = (data.get("actor") or "unknown").strip() or "unknown"
    return add_evidence_check(bundle, {
        "check_type": check_type,
        "status": "warning",
        "tool_name": "human_waiver",
        "output": reason,
        "metadata": {
            "waiver": True,
            "actor": actor,
            "reason": reason,
        },
    })


def add_evidence_approval(bundle: EvidenceBundle, data: dict) -> EvidenceCheck:
    actor = (data.get("actor") or "").strip()
    if not actor:
        raise ValueError("actor is required")
    reason = (data.get("reason") or data.get("summary") or "").strip()
    if not reason:
        raise ValueError("reason is required")
    return add_evidence_check(bundle, {
        "check_type": "human_approval",
        "status": "passed",
        "tool_name": "human_approval",
        "output": reason,
        "metadata": {
            "approval": True,
            "actor": actor,
            "reason": reason,
            "approved_bundle_id": str(bundle.id),
            "target_type": bundle.target_type,
            "target_id": str(bundle.target_id),
        },
    })


def create_evidence_repair_ticket(bundle: EvidenceBundle, data: dict) -> Ticket:
    """Create a repair intent from failed evidence and record the link on the bundle."""
    failing_checks = [
        check for check in list(bundle.checks or [])
        if check.status == "failed"
    ]
    if not failing_checks and bundle.status not in {"failed", "incomplete"}:
        raise ValueError("Repair requires a failed check or failed/incomplete evidence bundle")
    repair_policy = _repair_policy(data)
    _enforce_repair_retry_policy(bundle, repair_policy)

    title = (data.get("title") or "").strip() or _default_repair_title(bundle, failing_checks)
    description = (data.get("description") or "").strip() or _repair_description(bundle, failing_checks)
    ticket = Ticket(
        project_id=bundle.project_id,
        column_id=(data.get("column_id") or ("queued" if repair_policy["auto_dispatch"] else "backlog")).strip() or "backlog",
        title=title[:255],
        description=description,
        priority=(data.get("priority") or "high").strip() or "high",
        status="todo",
        associated_node_ids=data.get("associated_node_ids") or ["*"],
        depends_on_ticket_ids=data.get("depends_on_ticket_ids") or [],
        intent_status=(data.get("intent_status") or "ready").strip() or "ready",
        rationale=f"Created from failed evidence bundle {bundle.id}.",
        acceptance_criteria=_repair_acceptance_criteria(bundle, failing_checks),
        constraints="Preserve the audited evidence trail; do not delete or rewrite the failed evidence bundle.",
        risk_level="high",
        created_source="evidence_repair",
    )
    db.session.add(ticket)
    db.session.flush()
    dispatch_result = _dispatch_repair_ticket(ticket, repair_policy) if repair_policy["auto_dispatch"] else {
        "auto_dispatch": False,
        "dispatch_status": "not_requested",
    }

    add_evidence_check(bundle, {
        "check_type": "repair",
        "status": "passed",
        "tool_name": "evidence_repair_loop",
        "output": f"Created repair ticket {ticket.id}: {ticket.title}",
        "metadata": {
            "repair_ticket_id": str(ticket.id),
            "failing_check_ids": [str(check.id) for check in failing_checks],
            "repair_findings": _structured_repair_findings(failing_checks),
            "repair_policy": repair_policy,
            "repair_dispatch": dispatch_result,
        },
    })
    db.session.commit()
    _post_repair_event(bundle, ticket, failing_checks)
    return ticket


def collect_existing_target_evidence(project_id, data: dict) -> EvidenceBundle:
    """Create an evidence bundle from result fields already stored on a target."""
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    check_type = (data.get("check_type") or "unit").strip() or "unit"
    if not target_id:
        raise ValueError("target_id is required")

    if target_type == "composite_workspace":
        target = CompositeWorkspace.query.filter_by(project_id=project_id, id=target_id).first()
        if not target:
            raise ValueError("Composite Workspace not found")
        test_status = target.test_status or "skipped"
        bundle = create_evidence_bundle(project_id, {
            "target_type": target_type,
            "target_id": target_id,
            "base_hash": target.base_root_hash,
            "candidate_hash": target.composed_commit_hash,
            "selected_attempt_ids": target.selected_attempt_ids or [],
            "selected_leaf_hashes": target.selected_leaf_hashes or [],
            "status": _bundle_status_from_test_status(test_status),
            "risk_level": _risk_from_test_status(test_status),
            "summary": target.summary or f"Collected evidence from Composite Workspace {str(target.id)[:8]}",
        })
        add_evidence_check(bundle, {
            "check_type": check_type,
            "status": _check_status_from_test_status(test_status),
            "tool_name": "workspace_composer",
            "output": target.test_output or "",
            "metadata": {
                "changed_files": target.changed_files or [],
                "changed_file_count": len(target.changed_files or []),
                "source_status": target.status,
            },
        })
        return bundle

    if target_type == "ship_run":
        target = ShipRun.query.filter_by(project_id=project_id, id=target_id).first()
        if not target:
            raise ValueError("ShipRun not found")
        test_status = target.test_status or "skipped"
        bundle = create_evidence_bundle(project_id, {
            "target_type": target_type,
            "target_id": target_id,
            "base_hash": target.base_main_hash,
            "candidate_hash": target.composed_commit_hash or target.shipped_commit_hash,
            "status": _bundle_status_from_test_status(test_status),
            "risk_level": _risk_from_test_status(test_status),
            "summary": target.summary or f"Collected evidence from ShipRun {str(target.id)[:8]}",
        })
        add_evidence_check(bundle, {
            "check_type": check_type,
            "status": _check_status_from_test_status(test_status),
            "tool_name": "shipper",
            "output": target.test_output or "",
            "metadata": {
                "changed_files": target.changed_files or [],
                "changed_file_count": len(target.changed_files or []),
                "release_branch": target.release_branch,
                "release_pr_number": target.release_pr_number,
                "source_status": target.status,
            },
        })
        return bundle

    if target_type == "attempt":
        target = TicketAttempt.query.filter_by(project_id=project_id, id=target_id).first()
        if not target:
            raise ValueError("Attempt not found")
        passed = target.status in {"accepted", "composed", "release_pr_open", "shipped"} and not target.validation_error
        bundle = create_evidence_bundle(project_id, {
            "target_type": target_type,
            "target_id": target_id,
            "base_hash": target.base_hash,
            "candidate_hash": target.agenthub_commit_hash,
            "selected_attempt_ids": [str(target.id)],
            "selected_leaf_hashes": [target.agenthub_commit_hash] if target.agenthub_commit_hash else [],
            "status": "passed" if passed else "failed",
            "risk_level": "low" if passed else "high",
            "summary": target.summary or f"Collected evidence from attempt {str(target.id)[:8]}",
        })
        add_evidence_check(bundle, {
            "check_type": "validation",
            "status": "passed" if passed else "failed",
            "tool_name": "attempt_validator",
            "output": target.validation_error or "Attempt validation passed.",
            "metadata": {"attempt_status": target.status},
        })
        return bundle

    raise ValueError("target_type must be one of: attempt, ship_run, composite_workspace")


def run_command_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run a deterministic local command and store its result as evidence."""
    project = db.session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    if project.execution_mode != "local":
        raise ValueError("Command evidence requires a local project")

    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        raise ValueError("Project project_path must be a readable directory")

    command = _normalize_command(data.get("command"))
    check_type = (data.get("check_type") or "").strip()
    if not check_type:
        raise ValueError("check_type is required")
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")

    target_fields = _target_bundle_fields(project_id, target_type, target_id)
    cwd = _resolve_command_cwd(project_path, data.get("cwd"))
    timeout_seconds = _normalize_timeout(data.get("timeout_seconds"))
    sandbox = _normalize_sandbox(data.get("sandbox"))

    result = _run_local_command(command, cwd, timeout_seconds, project_path=project_path, sandbox=sandbox)
    artifacts = _normalize_artifacts(data.get("artifacts"), project_path=project_path, cwd=cwd)

    check_status = "failed" if result["timed_out"] or result["exit_code"] not in (0, None) else "passed"
    bundle_status = "passed" if check_status == "passed" else "failed"
    risk_level = "low" if check_status == "passed" else "high"
    command_text = " ".join(shlex.quote(part) for part in command)

    bundle = create_evidence_bundle(project_id, {
        **target_fields,
        "status": bundle_status,
        "risk_level": risk_level,
        "summary": (data.get("summary") or f"{check_type} command evidence {check_status}").strip(),
    })
    extra_metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    add_evidence_check(bundle, {
        "check_type": check_type,
        "status": check_status,
        "tool_name": (data.get("tool_name") or command[0]).strip(),
        "command": command_text,
        "output": result["output"],
        "metadata": {
            **extra_metadata,
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "duration_seconds": result["duration"],
            "cwd": cwd,
            "sandbox": result["sandbox"],
            **({"artifacts": artifacts} if artifacts else {}),
        },
        "artifact_url": _primary_artifact_ref(artifacts, data.get("artifact_url")),
        "started_at": result["started"],
        "finished_at": result["finished"],
    })
    return bundle


def run_check_suite_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run configured deterministic evidence commands for a local project target."""
    project = db.session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    if project.execution_mode != "local":
        raise ValueError("Check suite evidence requires a local project")

    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        raise ValueError("Project project_path must be a readable directory")

    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    suites = data.get("check_suites")
    if suites is None:
        suites = normalize_verification_policy(project.verification_policy).get("check_suites") or []
    if not suites:
        raise ValueError("No check_suites configured")
    suites = [_normalize_check_suite_item(item) for item in suites]

    results = []
    for index, item in enumerate(suites):
        command = _normalize_command(item.get("command"))
        cwd = _resolve_command_cwd(project_path, item.get("cwd"))
        timeout_seconds = _normalize_timeout(item.get("timeout_seconds") or data.get("timeout_seconds"))
        sandbox = _normalize_sandbox(item.get("sandbox") if item.get("sandbox") is not None else data.get("sandbox"))
        result = _run_local_command(command, cwd, timeout_seconds, project_path=project_path, sandbox=sandbox)
        artifacts = _normalize_artifacts(item.get("artifacts"), project_path=project_path, cwd=cwd)
        status = "failed" if result["timed_out"] or result["exit_code"] not in (0, None) else "passed"
        results.append((index, item, command, cwd, timeout_seconds, sandbox, artifacts, result, status))

    all_passed = all(item[8] == "passed" for item in results)
    bundle = create_evidence_bundle(project_id, {
        **target_fields,
        "status": "passed" if all_passed else "failed",
        "risk_level": "low" if all_passed else "high",
        "summary": (
            data.get("summary")
            or f"Configured check suite {'passed' if all_passed else 'failed'}"
        ).strip(),
    })

    for index, item, command, cwd, timeout_seconds, sandbox, artifacts, result, status in results:
        add_evidence_check(bundle, {
            "check_type": item["check_type"],
            "status": status,
            "tool_name": item.get("tool_name") or command[0],
            "command": " ".join(shlex.quote(part) for part in command),
            "output": result["output"],
            "metadata": {
                "suite": True,
                "suite_index": index,
                "exit_code": result["exit_code"],
                "timed_out": result["timed_out"],
                "duration_seconds": result["duration"],
                "timeout_seconds": timeout_seconds,
                "cwd": cwd,
                "sandbox": result["sandbox"],
                **({"artifacts": artifacts} if artifacts else {}),
            },
            "artifact_url": _primary_artifact_ref(artifacts),
            "started_at": result["started"],
            "finished_at": result["finished"],
        })
    return bundle


def run_browser_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run a browser/e2e evidence command with standard Playwright artifact refs."""
    browser_data = _normalize_browser_evidence_request(project_id, data)
    preview_runtime = None
    try:
        preview_runtime = _start_preview_supervision(project_id, browser_data)
        bundle = run_command_evidence(project_id, browser_data)
    finally:
        lifecycle_updates = _stop_preview_supervision(preview_runtime)
    metadata = browser_data.get("metadata") if isinstance(browser_data.get("metadata"), dict) else {}
    if lifecycle_updates:
        metadata.update(lifecycle_updates)
        _update_browser_check_metadata(bundle, browser_data.get("check_type"), lifecycle_updates)
    if metadata.get("preview_required") and not metadata.get("preview_ready"):
        bundle.status = "failed"
        bundle.risk_level = "high"
        for check in list(bundle.checks or []):
            if check.check_type == browser_data.get("check_type"):
                check.status = "failed"
                suffix = "Preview environment was required but not ready."
                check.output = f"{check.output}\n{suffix}"[:12000] if check.output else suffix
        db.session.commit()
    if metadata.get("preview_launch_required") and not metadata.get("preview_command"):
        bundle.status = "failed"
        bundle.risk_level = "high"
        for check in list(bundle.checks or []):
            if check.check_type == browser_data.get("check_type"):
                check.status = "failed"
                suffix = "Preview process command was required but unavailable."
                check.output = f"{check.output}\n{suffix}"[:12000] if check.output else suffix
        db.session.commit()
    if (
        metadata.get("preview_launch_required")
        and metadata.get("preview_supervision_enabled")
        and metadata.get("preview_command")
        and not metadata.get("preview_ready")
    ):
        bundle.status = "failed"
        bundle.risk_level = "high"
        for check in list(bundle.checks or []):
            if check.check_type == browser_data.get("check_type"):
                check.status = "failed"
                suffix = "Managed preview process did not become ready."
                check.output = f"{check.output}\n{suffix}"[:12000] if check.output else suffix
        db.session.commit()
    return bundle


def run_replay_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run production-like replay/contract evidence and store diff artifacts."""
    replay_data = _normalize_replay_evidence_request(project_id, data)
    bundle = run_command_evidence(project_id, replay_data)
    metadata = replay_data.get("metadata") if isinstance(replay_data.get("metadata"), dict) else {}
    replay_failure = _replay_gate_failure(metadata)
    if replay_failure:
        bundle.status = "failed"
        bundle.risk_level = "high"
        for check in list(bundle.checks or []):
            if check.check_type == replay_data.get("check_type"):
                check.status = "failed"
                if check.output:
                    check.output = f"{check.output}\n{replay_failure}"[:12000]
                else:
                    check.output = replay_failure
        db.session.commit()
    return bundle


def run_mutation_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run mutation-testing evidence with standard mutation report artifacts."""
    mutation_data = _normalize_mutation_evidence_request(project_id, data)
    return run_command_evidence(project_id, mutation_data)


def run_property_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run property-based testing evidence with standard property report artifacts."""
    property_data = _normalize_property_evidence_request(project_id, data)
    return run_command_evidence(project_id, property_data)


def run_llm_review_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run or record structured LLM review findings as evidence."""
    project = db.session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")

    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    reviewer = (data.get("reviewer") or data.get("tool_name") or "").strip()
    if not reviewer:
        raise ValueError("reviewer is required")

    command = data.get("command")
    explicit_findings = data.get("findings")
    if command in (None, "") and explicit_findings is None:
        raise ValueError("command or findings is required")

    result = None
    artifacts = []
    command_text = None
    started = datetime.now(timezone.utc)
    finished = started
    output = ""
    command_failed = False
    parse_error = None

    if command not in (None, ""):
        if project.execution_mode != "local":
            raise ValueError("Command-backed LLM review evidence requires a local project")
        project_path = os.path.abspath((project.project_path or "").strip())
        if not project_path or not os.path.isdir(project_path):
            raise ValueError("Project project_path must be a readable directory")
        command_parts = _normalize_command(command)
        cwd = _resolve_command_cwd(project_path, data.get("cwd"))
        timeout_seconds = _normalize_timeout(data.get("timeout_seconds"))
        sandbox = _normalize_sandbox(data.get("sandbox"))
        result = _run_local_command(command_parts, cwd, timeout_seconds, project_path=project_path, sandbox=sandbox)
        artifacts = _normalize_artifacts(_llm_review_artifacts(data), project_path=project_path, cwd=cwd)
        command_text = " ".join(shlex.quote(part) for part in command_parts)
        started = result["started"]
        finished = result["finished"]
        output = result["output"]
        command_failed = result["timed_out"] or result["exit_code"] not in (0, None)
        if explicit_findings is not None:
            raw_findings = explicit_findings
        else:
            try:
                raw_findings = _findings_from_output(output)
            except ValueError as exc:
                raw_findings = []
                command_failed = True
                parse_error = str(exc)
    else:
        artifacts = _normalize_artifacts(_llm_review_artifacts(data))
        raw_findings = explicit_findings

    findings = _normalize_llm_findings(raw_findings)
    status = _llm_review_status(findings, command_failed)
    summary = (data.get("summary") or f"{reviewer} LLM review {status}").strip()
    risk_level = "high" if status == "failed" else "medium" if status == "warning" else "low"

    bundle = create_evidence_bundle(project_id, {
        **target_fields,
        "status": status,
        "risk_level": risk_level,
        "summary": summary,
    })
    metadata = {
        **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
        "llm_review": True,
        "reviewer": reviewer,
        "model": (data.get("model") or "").strip() or None,
        "prompt_version": (data.get("prompt_version") or "").strip() or None,
        "findings": findings,
        "finding_counts": dict(Counter(finding["severity"] for finding in findings)),
        "blocking_findings": sum(1 for finding in findings if finding["blocking"]),
        "structured": True,
    }
    if parse_error:
        metadata["parse_error"] = parse_error
    if result:
        metadata.update({
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "duration_seconds": result["duration"],
            "cwd": cwd,
            "sandbox": result["sandbox"],
        })

    add_evidence_check(bundle, {
        "check_type": "llm_review",
        "status": status,
        "tool_name": reviewer,
        "command": command_text,
        "output": output or _llm_review_output(findings),
        "metadata": metadata,
        "artifacts": artifacts,
        "started_at": started,
        "finished_at": finished,
    })
    return bundle


def run_test_adequacy_evidence(project_id, data: dict) -> EvidenceBundle:
    """Run or record structured generated-test adequacy findings as evidence."""
    project = db.session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")

    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    command = data.get("command")
    explicit_findings = data.get("findings") if data.get("findings") is not None else data.get("assessments")
    generate_candidates = bool(data.get("generate_candidate_tests", False))
    if command in (None, "") and explicit_findings is None and not generate_candidates:
        raise ValueError("command or findings is required")

    acceptance_criteria = _string_list(data.get("acceptance_criteria"))
    if generate_candidates and not acceptance_criteria:
        acceptance_criteria = _target_acceptance_criteria(project_id, target_fields)
    generated_candidates = _candidate_tests_from_criteria(data, acceptance_criteria) if generate_candidates else []
    written_tests = _write_generated_test_files(project, data, generated_candidates) if data.get("write_generated_tests") else []

    result = None
    artifacts = []
    command_text = None
    started = datetime.now(timezone.utc)
    finished = started
    output = ""
    command_failed = False
    parse_error = None

    if command not in (None, ""):
        if project.execution_mode != "local":
            raise ValueError("Command-backed test adequacy evidence requires a local project")
        project_path = os.path.abspath((project.project_path or "").strip())
        if not project_path or not os.path.isdir(project_path):
            raise ValueError("Project project_path must be a readable directory")
        command_parts = _normalize_command(command)
        cwd = _resolve_command_cwd(project_path, data.get("cwd"))
        timeout_seconds = _normalize_timeout(data.get("timeout_seconds"))
        sandbox = _normalize_sandbox(data.get("sandbox"))
        result = _run_local_command(command_parts, cwd, timeout_seconds, project_path=project_path, sandbox=sandbox)
        artifacts = _normalize_artifacts(_test_adequacy_artifacts(data), project_path=project_path, cwd=cwd)
        command_text = " ".join(shlex.quote(part) for part in command_parts)
        started = result["started"]
        finished = result["finished"]
        output = result["output"]
        command_failed = result["timed_out"] or result["exit_code"] not in (0, None)
        if explicit_findings is not None:
            raw_findings = explicit_findings
        else:
            try:
                raw_findings = _test_adequacy_findings_from_output(output)
            except ValueError as exc:
                raw_findings = []
                command_failed = True
                parse_error = str(exc)
    else:
        artifacts = _normalize_artifacts(_test_adequacy_artifacts(data))
        raw_findings = explicit_findings or []

    findings = _normalize_test_adequacy_findings(raw_findings)
    status = _test_adequacy_status(findings, command_failed)
    risk_level = "high" if status == "failed" else "medium" if status == "warning" else "low"
    generated_test_paths = _string_list(data.get("generated_test_paths"))
    if written_tests:
        generated_test_paths = list(dict.fromkeys([*generated_test_paths, *[item["path"] for item in written_tests]]))

    bundle = create_evidence_bundle(project_id, {
        **target_fields,
        "status": status,
        "risk_level": risk_level,
        "summary": (data.get("summary") or f"Test adequacy {status}").strip(),
    })
    metadata = {
        **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
        "test_adequacy": True,
        "generated_test_paths": generated_test_paths,
        "acceptance_criteria": acceptance_criteria,
        "generated_test_candidates": generated_candidates,
        "generated_test_candidate_count": len(generated_candidates),
        "generated_test_files_written": written_tests,
        "generated_test_file_count": len(written_tests),
        "findings": findings,
        "finding_counts": dict(Counter(finding["severity"] for finding in findings)),
        "blocking_findings": sum(1 for finding in findings if finding["blocking"]),
        "uncovered_criteria": [
            finding["criterion"] for finding in findings
            if finding.get("criterion") and not finding["covered"]
        ],
        "weakened_existing_tests": any(finding["weakened_existing_tests"] for finding in findings),
        "structured": True,
    }
    if parse_error:
        metadata["parse_error"] = parse_error
    if result:
        metadata.update({
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "duration_seconds": result["duration"],
            "cwd": cwd,
            "sandbox": result["sandbox"],
        })

    add_evidence_check(bundle, {
        "check_type": "test_adequacy",
        "status": status,
        "tool_name": (data.get("tool_name") or "test_adequacy").strip(),
        "command": command_text,
        "output": output or _test_adequacy_output(findings),
        "metadata": metadata,
        "artifacts": artifacts,
        "started_at": started,
        "finished_at": finished,
    })
    return bundle


def rerun_failed_evidence_checks(bundle: EvidenceBundle, data: dict) -> EvidenceBundle:
    """Safely rerun failed command-backed checks from an evidence bundle."""
    project = db.session.get(Project, bundle.project_id)
    if not project:
        raise ValueError("Project not found")
    if project.execution_mode != "local":
        raise ValueError("Evidence rerun requires a local project")

    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        raise ValueError("Project project_path must be a readable directory")

    requested_ids = {
        str(check_id) for check_id in (data.get("check_ids") or [])
        if str(check_id).strip()
    }
    failed_checks = [
        check for check in list(bundle.checks or [])
        if check.status == "failed" and check.command and (not requested_ids or str(check.id) in requested_ids)
    ]
    if not failed_checks:
        raise ValueError("No failed command-backed evidence checks are safe to rerun")

    timeout_seconds = _normalize_timeout(data.get("timeout_seconds"))
    target_fields = {
        "target_type": bundle.target_type,
        "target_id": str(bundle.target_id),
        "base_hash": bundle.base_hash,
        "candidate_hash": bundle.candidate_hash,
        "selected_attempt_ids": bundle.selected_attempt_ids or [],
        "selected_leaf_hashes": bundle.selected_leaf_hashes or [],
    }
    rerun_results = []
    for check in failed_checks:
        metadata = check.check_metadata or {}
        cwd = _resolve_rerun_cwd(project_path, metadata.get("cwd"))
        command = shlex.split(check.command or "")
        if not command:
            raise ValueError(f"Evidence check {check.id} has no rerunnable command")
        sandbox = _normalize_sandbox(data.get("sandbox") or metadata.get("sandbox"))
        result = _run_local_command(command, cwd, timeout_seconds, project_path=project_path, sandbox=sandbox)
        status = "failed" if result["timed_out"] or result["exit_code"] not in (0, None) else "passed"
        rerun_results.append((check, command, cwd, sandbox, result, status))

    all_passed = all(item[5] == "passed" for item in rerun_results)
    rerun_bundle = create_evidence_bundle(bundle.project_id, {
        **target_fields,
        "status": "passed" if all_passed else "failed",
        "risk_level": "low" if all_passed else "high",
        "summary": (
            data.get("summary")
            or f"Rerun {len(rerun_results)} failed evidence check{'s' if len(rerun_results) != 1 else ''}"
        ).strip(),
    })

    for original, command, cwd, sandbox, result, status in rerun_results:
        add_evidence_check(rerun_bundle, {
            "check_type": original.check_type,
            "status": status,
            "tool_name": original.tool_name or (command[0] if command else "rerun"),
            "command": " ".join(shlex.quote(part) for part in command),
            "output": result["output"],
            "artifact_url": original.artifact_url,
            "metadata": {
                "exit_code": result["exit_code"],
                "timed_out": result["timed_out"],
                "duration_seconds": result["duration"],
                "cwd": cwd,
                "sandbox": result["sandbox"],
                "rerun": True,
                "rerun_of_bundle_id": str(bundle.id),
                "rerun_of_check_id": str(original.id),
                **({"artifacts": (original.check_metadata or {}).get("artifacts")} if (original.check_metadata or {}).get("artifacts") else {}),
            },
            "started_at": result["started"],
            "finished_at": result["finished"],
        })
    return rerun_bundle


def compare_candidate_evidence(project_id, data: dict) -> EvidenceBundle:
    """Compare a target candidate commit against its stable root/base commit."""
    project = db.session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    if project.execution_mode != "local":
        raise ValueError("Comparison evidence requires a local project")

    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        raise ValueError("Project project_path must be a readable directory")

    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    base_hash = (data.get("base_hash") or target_fields.get("base_hash") or "").strip()
    candidate_hash = (data.get("candidate_hash") or target_fields.get("candidate_hash") or "").strip()
    if not base_hash:
        raise ValueError("base_hash is required")
    if not candidate_hash:
        raise ValueError("candidate_hash is required")

    timeout_seconds = _normalize_timeout(data.get("timeout_seconds") or 120)
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()

    name_status = _run_git(["diff", "--name-status", base_hash, candidate_hash], project_path, timeout_seconds)
    numstat = _run_git(["diff", "--numstat", base_hash, candidate_hash], project_path, timeout_seconds)
    summary = _run_git(["diff", "--shortstat", base_hash, candidate_hash], project_path, timeout_seconds)

    finished = datetime.now(timezone.utc)
    duration = round(time.monotonic() - started_clock, 3)
    failed = name_status["returncode"] != 0 or numstat["returncode"] != 0 or summary["returncode"] != 0
    changed_files = _parse_name_status(name_status["stdout"]) if not failed else []
    line_counts = _parse_numstat(numstat["stdout"]) if not failed else {}
    additions = sum(item["additions"] for item in line_counts.values())
    deletions = sum(item["deletions"] for item in line_counts.values())
    changed_file_names = [item["path"] for item in changed_files]
    status_counts = dict(Counter(item["status"] for item in changed_files))

    output = (
        (summary["stdout"] or "").strip()
        or f"{len(changed_files)} files changed, {additions} insertions(+), {deletions} deletions(-)"
    )
    if failed:
        output = "\n".join(
            text for text in [
                name_status["stderr"],
                numstat["stderr"],
                summary["stderr"],
            ]
            if text
        )[:12000] or "Git comparison failed."

    check_status = "failed" if failed else "passed"
    bundle = create_evidence_bundle(project_id, {
        **target_fields,
        "base_hash": base_hash,
        "candidate_hash": candidate_hash,
        "status": "failed" if failed else "passed",
        "risk_level": "high" if failed else _risk_from_diff(len(changed_files), additions + deletions),
        "summary": (data.get("summary") or f"Stable-root comparison {check_status}").strip(),
    })
    add_evidence_check(bundle, {
        "check_type": "diff",
        "status": check_status,
        "tool_name": "git_diff",
        "command": f"git diff --name-status {shlex.quote(base_hash)} {shlex.quote(candidate_hash)}",
        "output": output,
        "metadata": {
            "base_hash": base_hash,
            "candidate_hash": candidate_hash,
            "changed_files": changed_file_names,
            "changed_file_count": len(changed_file_names),
            "file_status_counts": status_counts,
            "line_counts": line_counts,
            "additions": additions,
            "deletions": deletions,
            "duration_seconds": duration,
        },
        "started_at": started,
        "finished_at": finished,
    })
    return bundle


def create_evidence_run(project_id, data: dict) -> EvidenceRun:
    """Queue evidence work so UI requests do not execute long-running checks directly."""
    project = db.session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    run_type = (data.get("run_type") or data.get("kind") or "").strip()
    if run_type not in VALID_EVIDENCE_RUN_TYPES:
        raise ValueError("run_type must be one of: " + ", ".join(sorted(VALID_EVIDENCE_RUN_TYPES)))

    request_data = _normalize_evidence_run_request(project_id, run_type, data)
    run = EvidenceRun(
        project_id=project_id,
        run_type=run_type,
        status="queued",
        target_type=request_data["target_type"],
        target_id=request_data["target_id"],
        check_type=request_data.get("check_type"),
        request_data=request_data,
    )
    db.session.add(run)
    db.session.commit()
    return run


def claim_next_evidence_run(project_id=None) -> EvidenceRun | None:
    """Claim the oldest queued evidence run for worker execution."""
    query = EvidenceRun.query.filter_by(status="queued")
    if project_id is not None:
        query = query.filter_by(project_id=project_id)
    run = query.order_by(EvidenceRun.created_at.asc()).first()
    if not run:
        return None
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    run.error = None
    db.session.commit()
    return run


def execute_evidence_run(run: EvidenceRun) -> EvidenceRun:
    """Execute a claimed evidence run and attach the resulting bundle."""
    if run.status == "queued":
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.session.commit()
    if run.status != "running":
        raise ValueError("Evidence run must be queued or running")

    try:
        if run.run_type == "command":
            bundle = run_command_evidence(run.project_id, run.request_data or {})
        elif run.run_type == "suite":
            bundle = run_check_suite_evidence(run.project_id, run.request_data or {})
        elif run.run_type == "browser":
            bundle = run_browser_evidence(run.project_id, run.request_data or {})
        elif run.run_type == "replay":
            bundle = run_replay_evidence(run.project_id, run.request_data or {})
        elif run.run_type == "mutation":
            bundle = run_mutation_evidence(run.project_id, run.request_data or {})
        elif run.run_type == "property":
            bundle = run_property_evidence(run.project_id, run.request_data or {})
        elif run.run_type == "llm_review":
            bundle = run_llm_review_evidence(run.project_id, run.request_data or {})
        elif run.run_type == "test_adequacy":
            bundle = run_test_adequacy_evidence(run.project_id, run.request_data or {})
        else:
            raise ValueError("Unsupported evidence run_type")
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        return run

    run.evidence_bundle_id = bundle.id
    run.status = "completed"
    run.error = None
    run.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    return run


def complete_external_evidence_run(run: EvidenceRun, data: dict) -> EvidenceRun:
    """Attach evidence produced by a non-local worker to a claimed run."""
    if run.status == "queued":
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.session.commit()
    if run.status != "running":
        raise ValueError("Evidence run must be queued or running")

    if run.run_type == "llm_review":
        checks = _external_llm_review_checks(run, data)
    else:
        checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks must be a non-empty list")

    target_fields = _target_bundle_fields(run.project_id, run.target_type, str(run.target_id))
    bundle = create_evidence_bundle(run.project_id, {
        **target_fields,
        "base_hash": (data.get("base_hash") or target_fields.get("base_hash") or "").strip() or None,
        "candidate_hash": (data.get("candidate_hash") or target_fields.get("candidate_hash") or "").strip() or None,
        "selected_attempt_ids": data.get("selected_attempt_ids") or target_fields.get("selected_attempt_ids") or [],
        "selected_leaf_hashes": data.get("selected_leaf_hashes") or target_fields.get("selected_leaf_hashes") or [],
        "status": (data.get("status") or _bundle_status_from_checks(checks)).strip(),
        "risk_level": (data.get("risk_level") or _risk_from_external_checks(checks)).strip(),
        "summary": (data.get("summary") or f"External {run.run_type} evidence").strip(),
    })

    worker_id = (data.get("worker_id") or data.get("agent_id") or "").strip() or None
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("checks entries must be objects")
        metadata = check.get("metadata") if isinstance(check.get("metadata"), dict) else {}
        add_evidence_check(bundle, {
            **check,
            "check_type": (check.get("check_type") or run.check_type or run.run_type).strip(),
            "metadata": {
                **metadata,
                "external_worker": True,
                "evidence_run_id": str(run.id),
                "run_type": run.run_type,
                **({"worker_id": worker_id} if worker_id else {}),
            },
        })

    run.evidence_bundle_id = bundle.id
    run.status = "completed"
    run.error = None
    run.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    return run


def fail_external_evidence_run(run: EvidenceRun, data: dict) -> EvidenceRun:
    """Mark externally executed evidence work as failed without fabricating a bundle."""
    if run.status not in {"queued", "running"}:
        raise ValueError("Only queued or running evidence runs can be failed")
    reason = (data.get("error") or data.get("reason") or "").strip()
    if not reason:
        raise ValueError("error is required")
    if run.status == "queued" and not run.started_at:
        run.started_at = datetime.now(timezone.utc)
    run.status = "failed"
    run.error = reason
    run.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    return run


def cancel_evidence_run(run: EvidenceRun) -> EvidenceRun:
    if run.status not in {"queued", "running"}:
        raise ValueError("Only queued or running evidence runs can be canceled")
    run.status = "canceled"
    run.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    return run


def _normalize_evidence_run_request(project_id, run_type: str, data: dict) -> dict:
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    request_data = {
        "target_type": target_fields["target_type"],
        "target_id": target_fields["target_id"],
    }
    for key in ("summary", "cwd", "timeout_seconds"):
        if data.get(key) not in (None, ""):
            request_data[key] = data.get(key)
    if data.get("sandbox") is not None:
        request_data["sandbox"] = _normalize_sandbox(data.get("sandbox"))

    if run_type in {"command", "browser", "replay", "mutation", "property"}:
        check_type = (data.get("check_type") or "").strip()
        if not check_type and run_type == "browser":
            check_type = "e2e"
        if not check_type and run_type == "replay":
            check_type = "replay"
        if not check_type and run_type == "mutation":
            check_type = "mutation"
        if not check_type and run_type == "property":
            check_type = "property"
        if not check_type:
            raise ValueError("check_type is required")
        request_data["check_type"] = check_type
        request_data["command"] = data.get("command")
        _normalize_command(request_data["command"])
        if run_type == "browser":
            request_data = _normalize_browser_evidence_request(project_id, {**data, **request_data})
        if run_type == "replay":
            request_data = _normalize_replay_evidence_request(project_id, {**data, **request_data})
        if run_type == "mutation":
            request_data = _normalize_mutation_evidence_request(project_id, {**data, **request_data})
        if run_type == "property":
            request_data = _normalize_property_evidence_request(project_id, {**data, **request_data})
        if data.get("tool_name"):
            request_data["tool_name"] = str(data.get("tool_name")).strip()
        if data.get("artifacts") is not None:
            request_data["artifacts"] = _normalize_artifacts(data.get("artifacts"))
        return request_data

    if run_type == "llm_review":
        request_data = _normalize_llm_review_evidence_request(project_id, {**data, **request_data})
        return request_data

    if run_type == "test_adequacy":
        request_data = _normalize_test_adequacy_evidence_request(project_id, {**data, **request_data})
        return request_data

    suites = data.get("check_suites")
    if suites is not None:
        if not isinstance(suites, list):
            raise ValueError("check_suites must be a list")
        request_data["check_suites"] = [_normalize_check_suite_item(item) for item in suites]
    return request_data


def _normalize_browser_evidence_request(project_id, data: dict) -> dict:
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    command = data.get("command") or data.get("playwright_command")
    _normalize_command(command)
    check_type = (data.get("check_type") or "e2e").strip() or "e2e"
    preview_context = _target_preview_context(project_id, target_fields["target_type"], target_fields["target_id"])
    preview_url = (data.get("preview_url") or preview_context.get("preview_url") or "").strip()
    preview_process = _preview_process_context(project_id, data, preview_context)
    artifacts = _browser_artifacts(data)
    retry_count = _optional_int(data.get("retry_count"), "retry_count")
    shard = _browser_shard(data.get("shard"))
    console_errors = _string_list(data.get("console_errors"))
    network_failures = _string_list(data.get("network_failures"))
    failure_artifacts_only = bool(data.get("failure_artifacts_only", True))
    preview_required = bool(data.get("preview_required", False))
    preview_launch_required = bool(data.get("preview_launch_required", False))
    preview_ready = bool(preview_url) and (
        target_fields["target_type"] != "composite_workspace"
        or preview_context.get("status") in {"preview_ready", "blessed", "snapshot_candidate"}
    )
    metadata = {
        **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
        "browser": True,
        "runner": "playwright",
        "preview_url": preview_url or None,
        "preview_status": preview_context.get("status"),
        "preview_process_status": preview_context.get("preview_status"),
        "preview_ready": preview_ready,
        "preview_required": preview_required,
        "preview_source": "request" if data.get("preview_url") else preview_context.get("source"),
        "preview_command": preview_process.get("command"),
        "preview_command_source": preview_process.get("source"),
        "preview_launch_required": preview_launch_required,
        "preview_managed": bool(preview_process.get("command")),
        "preview_supervision_enabled": bool(data.get("preview_supervision_enabled", False)),
        "preview_error": preview_context.get("preview_error"),
        "preview_ready_timeout_seconds": _optional_int(data.get("preview_ready_timeout_seconds"), "preview_ready_timeout_seconds"),
        "retry_count": retry_count,
        "flake": retry_count > 0,
        "shard": shard,
        "console_errors": console_errors,
        "network_failures": network_failures,
        "failure_artifacts_only": failure_artifacts_only,
        "artifact_defaults": not bool(data.get("artifacts")),
    }

    out = {
        "target_type": target_fields["target_type"],
        "target_id": target_fields["target_id"],
        "check_type": check_type,
        "command": command,
        "tool_name": (data.get("tool_name") or "playwright").strip(),
        "summary": (data.get("summary") or f"{check_type} browser evidence").strip(),
        "metadata": metadata,
        "artifacts": artifacts,
    }
    for key in ("cwd", "timeout_seconds", "sandbox"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    for key in ("preview_command", "preview_launch_required", "preview_ready_timeout_seconds", "auto_detect_preview_command", "preview_supervision_enabled"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    return out


def _start_preview_supervision(project_id, browser_data: dict) -> dict | None:
    metadata = browser_data.get("metadata") if isinstance(browser_data.get("metadata"), dict) else {}
    if not metadata.get("preview_supervision_enabled"):
        metadata["preview_lifecycle_status"] = "not_requested"
        return None

    command = _optional_command(metadata.get("preview_command"))
    if not command:
        metadata["preview_lifecycle_status"] = "missing_command"
        return None
    preview_url = (metadata.get("preview_url") or "").strip()
    if not preview_url:
        metadata["preview_lifecycle_status"] = "missing_url"
        return None

    project = db.session.get(Project, project_id)
    if not project or project.execution_mode != "local":
        metadata["preview_lifecycle_status"] = "not_local"
        return None
    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        metadata["preview_lifecycle_status"] = "missing_project_path"
        return None

    cwd = _resolve_command_cwd(project_path, browser_data.get("cwd"))
    sandbox = _normalize_sandbox(browser_data.get("sandbox"))
    temp_home = tempfile.TemporaryDirectory(prefix="terarchitect-preview-")
    started_at = datetime.now(timezone.utc)
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_sandbox_env(sandbox, project_path, temp_home.name if sandbox["enabled"] else None),
        )
    except (OSError, ValueError) as exc:
        temp_home.cleanup()
        metadata.update({
            "preview_lifecycle_status": "failed_to_start",
            "preview_lifecycle_error": str(exc),
            "preview_started_at": started_at.isoformat(),
        })
        return None

    timeout_seconds = metadata.get("preview_ready_timeout_seconds") or 30
    metadata.update({
        "preview_lifecycle_status": "started",
        "preview_process_pid": process.pid,
        "preview_started_at": started_at.isoformat(),
        "preview_cwd": cwd,
    })
    ready = _wait_for_preview_url(preview_url, process, timeout_seconds)
    metadata.update(ready)
    if ready.get("preview_ready"):
        metadata["preview_lifecycle_status"] = "ready"
    elif ready.get("preview_process_exit_code") is not None:
        metadata["preview_lifecycle_status"] = "exited_early"
    else:
        metadata["preview_lifecycle_status"] = "timeout"
    return {"process": process, "temp_home": temp_home}


def _stop_preview_supervision(runtime: dict | None) -> dict:
    if not runtime:
        return {}
    process = runtime.get("process")
    temp_home = runtime.get("temp_home")
    stopped_at = datetime.now(timezone.utc)
    updates = {"preview_stopped_at": stopped_at.isoformat()}
    try:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                updates["preview_stop_forced"] = True
        if process:
            updates["preview_process_exit_code"] = process.poll()
            updates["preview_lifecycle_status"] = "stopped"
    finally:
        if temp_home:
            temp_home.cleanup()
    return updates


def _wait_for_preview_url(preview_url: str, process: subprocess.Popen, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + max(timeout_seconds, 1)
    last_error = None
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            return {
                "preview_ready": False,
                "preview_process_exit_code": exit_code,
                "preview_lifecycle_error": last_error or f"preview process exited with {exit_code}",
            }
        try:
            req = urllib_request.Request(preview_url, method="GET")
            with urllib_request.urlopen(req, timeout=1) as resp:
                if 200 <= getattr(resp, "status", 200) < 500:
                    return {
                        "preview_ready": True,
                        "preview_ready_at": datetime.now(timezone.utc).isoformat(),
                    }
        except (OSError, urllib_error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    return {
        "preview_ready": False,
        "preview_lifecycle_error": last_error or "preview readiness timed out",
    }


def _update_browser_check_metadata(bundle: EvidenceBundle, check_type: str, updates: dict) -> None:
    for check in list(bundle.checks or []):
        if check.check_type != check_type:
            continue
        metadata = check.check_metadata if isinstance(check.check_metadata, dict) else {}
        check.check_metadata = {**metadata, **updates}
    db.session.commit()


def _browser_artifacts(data: dict) -> list[dict]:
    if data.get("artifacts") is not None:
        return _normalize_artifacts(data.get("artifacts"))
    report_path = (data.get("report_path") or "playwright-report/index.html").strip()
    results_path = (data.get("results_path") or "test-results").strip()
    trace_path = (data.get("trace_path") or "test-results").strip()
    screenshot_path = (data.get("screenshot_path") or "").strip()
    video_path = (data.get("video_path") or "").strip()
    artifacts = [
        {"kind": "report", "label": "Playwright HTML report", "path": report_path},
        {"kind": "trace", "label": "Playwright test results", "path": results_path},
    ]
    if trace_path and trace_path != results_path:
        artifacts.append({"kind": "trace", "label": "Playwright trace", "path": trace_path})
    if screenshot_path:
        artifacts.append({"kind": "screenshot", "label": "Failure screenshot", "path": screenshot_path})
    if video_path:
        artifacts.append({"kind": "video", "label": "Failure video", "path": video_path})
    return artifacts


def _browser_shard(value: Any) -> dict | None:
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError("shard must be an object")
    index = _optional_int(value.get("index"), "shard.index")
    total = _optional_int(value.get("total"), "shard.total")
    if index <= 0 or total <= 0:
        raise ValueError("shard index and total must be positive")
    if index > total:
        raise ValueError("shard index cannot exceed total")
    return {"index": index, "total": total}


def _target_preview_url(project_id, target_type: str, target_id: str) -> str | None:
    return _target_preview_context(project_id, target_type, target_id).get("preview_url")


def _target_preview_context(project_id, target_type: str, target_id: str) -> dict:
    if target_type == "composite_workspace":
        workspace = CompositeWorkspace.query.filter_by(project_id=project_id, id=target_id).first()
        if not workspace:
            return {
                "preview_url": None,
                "status": None,
                "preview_status": None,
                "preview_command": [],
                "preview_error": None,
                "source": "missing_target",
            }
        return {
            "preview_url": workspace.preview_url,
            "status": workspace.status,
            "preview_status": workspace.preview_status,
            "preview_command": workspace.preview_command or [],
            "preview_error": workspace.preview_error,
            "source": "composite_workspace",
        }
    return {
        "preview_url": None,
        "status": None,
        "preview_status": None,
        "preview_command": [],
        "preview_error": None,
        "source": None,
    }


def _preview_process_context(project_id, data: dict, preview_context: dict) -> dict:
    explicit = _optional_command(data.get("preview_command"))
    if explicit:
        return {"command": explicit, "source": "request"}
    workspace_command = _optional_command(preview_context.get("preview_command"))
    if workspace_command:
        return {"command": workspace_command, "source": "composite_workspace"}
    if data.get("auto_detect_preview_command"):
        detected = _detect_preview_command(project_id, data.get("cwd"))
        if detected:
            return {"command": detected, "source": "package_json"}
    return {"command": [], "source": None}


def _optional_command(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = value.strip().split()
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value]
    else:
        raise ValueError("preview_command must be a string or list")
    return [part for part in parts if part]


def _detect_preview_command(project_id, cwd_value: Any = None) -> list[str]:
    project = db.session.get(Project, project_id)
    if not project or project.execution_mode != "local":
        return []
    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        return []
    cwd = _resolve_command_cwd(project_path, cwd_value)
    package_path = os.path.join(cwd, "package.json")
    if not os.path.isfile(package_path):
        return []
    try:
        with open(package_path, "r", encoding="utf-8") as fh:
            package = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not isinstance(scripts, dict):
        return []
    for script in ("preview", "dev", "start"):
        if isinstance(scripts.get(script), str) and scripts[script].strip():
            return ["npm", "run", script]
    return []


def _normalize_replay_evidence_request(project_id, data: dict) -> dict:
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    command = data.get("command") or data.get("replay_command")
    _normalize_command(command)
    check_type = (data.get("check_type") or "replay").strip() or "replay"
    traffic_path = (data.get("traffic_path") or data.get("fixture_path") or "").strip()
    contract_path = (data.get("contract_path") or data.get("openapi_path") or "").strip()
    replay_context = _target_replay_context(project_id, target_fields)
    base_url = (data.get("base_url") or data.get("stable_url") or "").strip()
    candidate_url = (data.get("candidate_url") or replay_context.get("candidate_url") or "").strip()
    candidate_url_source = "request" if data.get("candidate_url") else replay_context.get("candidate_url_source")
    candidate_service_ready = bool(candidate_url) and (
        target_fields["target_type"] != "composite_workspace"
        or candidate_url_source == "request"
        or replay_context.get("candidate_status") in {"preview_ready", "blessed", "snapshot_candidate"}
    )
    traffic_context = _captured_traffic_context(project_id, data, traffic_path, base_url, candidate_url)
    compared_endpoints = _string_list(data.get("compared_endpoints")) or traffic_context.get("traffic_endpoints", [])
    contract_validation = _contract_validation_context(project_id, data, contract_path, compared_endpoints)
    regressions = _replay_regressions(data)
    artifacts = _replay_artifacts(data)
    metadata = {
        **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
        "replay": True,
        "runner": "replay",
        "traffic_path": traffic_path or None,
        "contract_path": contract_path or None,
        "base_url": base_url or None,
        "candidate_url": candidate_url or None,
        "base_url_required": bool(data.get("base_url_required", False)),
        "candidate_url_required": bool(data.get("candidate_url_required", False)),
        "base_url_source": "request" if base_url else None,
        "candidate_url_source": candidate_url_source,
        "candidate_service_status": replay_context.get("candidate_status"),
        "candidate_service_ready": candidate_service_ready,
        "service_comparison_ready": bool(base_url) and candidate_service_ready,
        "target_base_hash": target_fields.get("base_hash"),
        "target_candidate_hash": target_fields.get("candidate_hash"),
        **traffic_context,
        "traffic_source": (data.get("traffic_source") or "").strip() or traffic_context.get("traffic_source"),
        "sample_count": _optional_int(data.get("sample_count"), "sample_count") or traffic_context.get("sample_count", 0),
        "traffic_parse_required": bool(data.get("traffic_parse_required", False)),
        "contract_compatible": data.get("contract_compatible") if isinstance(data.get("contract_compatible"), bool) else None,
        "contract_validation_required": bool(data.get("contract_validation_required") or data.get("contract_parse_required")),
        **contract_validation,
        "compared_endpoints": compared_endpoints,
        "regressions": regressions,
        "regression_counts": {key: len(value) for key, value in regressions.items()},
        "regression_detected": any(regressions.values()),
        "artifact_defaults": not bool(data.get("artifacts")),
    }

    out = {
        "target_type": target_fields["target_type"],
        "target_id": target_fields["target_id"],
        "check_type": check_type,
        "command": command,
        "tool_name": (data.get("tool_name") or "replay").strip(),
        "summary": (data.get("summary") or f"{check_type} evidence").strip(),
        "metadata": metadata,
        "artifacts": artifacts,
    }
    for key in ("cwd", "timeout_seconds", "sandbox"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    for key in (
        "traffic_path",
        "fixture_path",
        "contract_path",
        "openapi_path",
        "base_url",
        "stable_url",
        "candidate_url",
        "base_url_required",
        "candidate_url_required",
        "traffic_source",
        "sample_count",
        "traffic_parse_required",
        "generate_replay_manifest",
        "replay_manifest_path",
        "contract_compatible",
        "compared_endpoints",
        "status_code_regressions",
        "schema_regressions",
        "auth_regressions",
        "behavior_regressions",
        "contract_validation_required",
        "contract_parse_required",
        "diff_path",
        "report_path",
    ):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    return out


def _target_replay_context(project_id, target_fields: dict) -> dict:
    if target_fields.get("target_type") == "composite_workspace":
        preview_context = _target_preview_context(
            project_id,
            target_fields["target_type"],
            target_fields["target_id"],
        )
        return {
            "candidate_url": preview_context.get("preview_url"),
            "candidate_status": preview_context.get("status"),
            "candidate_url_source": preview_context.get("source"),
        }
    return {
        "candidate_url": None,
        "candidate_status": None,
        "candidate_url_source": None,
    }


def _captured_traffic_context(project_id, data: dict, traffic_path: str, base_url: str, candidate_url: str) -> dict:
    context = {
        "traffic_parsed": False,
        "traffic_parse_error": None,
        "traffic_entry_count": 0,
        "traffic_endpoints": [],
        "traffic_entries": [],
        "traffic_source": None,
        "replay_manifest_path": None,
        "replay_manifest_written": False,
        "sample_count": 0,
    }
    if not traffic_path:
        return context
    project = db.session.get(Project, project_id)
    if not project or project.execution_mode != "local":
        context["traffic_parse_error"] = "Traffic parsing requires a local project"
        return context
    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        context["traffic_parse_error"] = "Project project_path must be a readable directory"
        return context
    try:
        resolved = _resolve_project_existing_path(project_path, traffic_path, "Traffic path")
        entries = _load_captured_traffic_entries(resolved)
        manifest_path = None
        manifest_written = False
        if data.get("generate_replay_manifest"):
            manifest_path = (data.get("replay_manifest_path") or "replay-manifest.json").strip()
            manifest_resolved = _resolve_project_output_path(project_path, manifest_path, "replay manifest path")
            manifest = {
                "traffic_path": traffic_path,
                "base_url": base_url or None,
                "candidate_url": candidate_url or None,
                "entries": entries,
            }
            parent = os.path.dirname(manifest_resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(manifest_resolved, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, sort_keys=True)
                fh.write("\n")
            manifest_written = True
        endpoints = list(dict.fromkeys(entry["endpoint"] for entry in entries if entry.get("endpoint")))
        context.update({
            "traffic_parsed": True,
            "traffic_entry_count": len(entries),
            "traffic_endpoints": endpoints[:200],
            "traffic_entries": entries[:200],
            "traffic_source": _captured_traffic_source(resolved),
            "replay_manifest_path": manifest_path,
            "replay_manifest_written": manifest_written,
            "sample_count": len(entries),
        })
    except ValueError as exc:
        context["traffic_parse_error"] = str(exc)
    return context


def _load_captured_traffic_entries(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not parse traffic file: {exc}") from exc
    raw_entries = None
    if isinstance(parsed, dict):
        log = parsed.get("log")
        if isinstance(log, dict) and isinstance(log.get("entries"), list):
            raw_entries = log.get("entries")
        elif isinstance(parsed.get("entries"), list):
            raw_entries = parsed.get("entries")
        elif isinstance(parsed.get("requests"), list):
            raw_entries = parsed.get("requests")
    elif isinstance(parsed, list):
        raw_entries = parsed
    if not isinstance(raw_entries, list):
        raise ValueError("Traffic file must contain HAR log.entries, entries, requests, or a list")
    entries = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            continue
        request_data = item.get("request") if isinstance(item.get("request"), dict) else item
        method = str(request_data.get("method") or "GET").strip().upper()
        url = str(request_data.get("url") or request_data.get("path") or "").strip()
        parsed_url = urllib_parse.urlparse(url)
        path_value = parsed_url.path or url
        if not path_value.startswith("/"):
            path_value = f"/{path_value}"
        query = urllib_parse.parse_qsl(parsed_url.query, keep_blank_values=True)
        entries.append({
            "index": index,
            "method": method,
            "url": url or None,
            "path": path_value,
            "query": [{"name": key, "value": value} for key, value in query],
            "endpoint": f"{method} {path_value}",
        })
    if not entries:
        raise ValueError("Traffic file did not contain any replayable requests")
    return entries


def _captured_traffic_source(path: str) -> str:
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".har":
        return "har"
    if suffix == ".json":
        return "json"
    return "captured"


def _contract_validation_context(project_id, data: dict, contract_path: str, compared_endpoints: list[str]) -> dict:
    if not contract_path:
        return {
            "contract_parsed": False,
            "contract_parse_error": None,
            "contract_endpoint_count": 0,
            "contract_endpoints": [],
            "contract_missing_endpoints": [],
        }
    project = db.session.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    if project.execution_mode != "local":
        return {
            "contract_parsed": False,
            "contract_parse_error": "Contract parsing requires a local project",
            "contract_endpoint_count": 0,
            "contract_endpoints": [],
            "contract_missing_endpoints": [],
        }
    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        return {
            "contract_parsed": False,
            "contract_parse_error": "Project project_path must be a readable directory",
            "contract_endpoint_count": 0,
            "contract_endpoints": [],
            "contract_missing_endpoints": [],
        }
    try:
        resolved = _resolve_project_file(project_path, contract_path)
        endpoints = _openapi_contract_endpoints(resolved)
    except ValueError as exc:
        return {
            "contract_parsed": False,
            "contract_parse_error": str(exc),
            "contract_endpoint_count": 0,
            "contract_endpoints": [],
            "contract_missing_endpoints": [],
        }
    endpoint_set = set(endpoints)
    missing = [endpoint for endpoint in compared_endpoints if endpoint not in endpoint_set]
    return {
        "contract_parsed": True,
        "contract_parse_error": None,
        "contract_endpoint_count": len(endpoints),
        "contract_endpoints": endpoints[:200],
        "contract_missing_endpoints": missing,
    }


def _resolve_project_file(project_path: str, path_value: str) -> str:
    requested = os.path.abspath(os.path.join(project_path, path_value))
    common = os.path.commonpath([project_path, requested])
    if common != project_path:
        raise ValueError("Contract path is outside project_path")
    if not os.path.isfile(requested):
        raise ValueError("Contract file was not found")
    return requested


def _resolve_project_existing_path(project_path: str, path_value: str, label: str) -> str:
    if not path_value or os.path.isabs(path_value):
        raise ValueError(f"{label} must be a relative path")
    requested = os.path.abspath(os.path.join(project_path, path_value))
    common = os.path.commonpath([project_path, requested])
    if common != project_path:
        raise ValueError(f"{label} must be inside project_path")
    if not os.path.isfile(requested):
        raise ValueError(f"{label} was not found")
    return requested


def _openapi_contract_endpoints(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError(f"Could not read contract file: {exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        document = _load_yaml_contract(raw)
    paths = document.get("paths") if isinstance(document, dict) else None
    if not isinstance(paths, dict):
        raise ValueError("Contract does not contain an OpenAPI paths object")
    endpoints = []
    for route, methods in paths.items():
        if not isinstance(route, str) or not isinstance(methods, dict):
            continue
        for method in methods.keys():
            method_name = str(method).upper()
            if method_name in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"}:
                endpoints.append(f"{method_name} {route}")
    return sorted(endpoints)


def _load_yaml_contract(raw: str) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ValueError("Contract is not JSON and PyYAML is not installed") from exc
    try:
        parsed = yaml.safe_load(raw)
    except Exception as exc:
        raise ValueError(f"Could not parse contract file: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Contract file did not parse to an object")
    return parsed


def _replay_regressions(data: dict) -> dict:
    return {
        "status_code": _string_list(data.get("status_code_regressions")),
        "schema": _string_list(data.get("schema_regressions")),
        "auth": _string_list(data.get("auth_regressions")),
        "behavior": _string_list(data.get("behavior_regressions")),
    }


def _replay_has_regressions(metadata: dict) -> bool:
    regressions = metadata.get("regressions") if isinstance(metadata, dict) else None
    if isinstance(regressions, dict) and any(regressions.values()):
        return True
    if metadata.get("contract_missing_endpoints"):
        return True
    return metadata.get("contract_compatible") is False


def _replay_gate_failure(metadata: dict) -> str | None:
    if _replay_has_regressions(metadata):
        return "Replay regressions detected."
    if metadata.get("traffic_parse_required") and not metadata.get("traffic_parsed"):
        return "Replay traffic parsing was required but captured traffic could not be parsed."
    if metadata.get("contract_validation_required") and not metadata.get("contract_parsed"):
        return "Replay contract validation was required but the contract could not be parsed."
    if metadata.get("candidate_url_required") and not metadata.get("candidate_service_ready"):
        return "Replay candidate service was required but not ready."
    if metadata.get("base_url_required") and not metadata.get("base_url"):
        return "Replay stable base service URL was required but missing."
    return None


def _replay_artifacts(data: dict) -> list[dict]:
    if data.get("artifacts") is not None:
        return _normalize_artifacts(data.get("artifacts"))
    diff_path = (data.get("diff_path") or "replay-diff.json").strip()
    report_path = (data.get("report_path") or "replay-report.json").strip()
    return [
        {"kind": "diff", "label": "Replay diff", "path": diff_path},
        {"kind": "report", "label": "Replay report", "path": report_path},
    ]


def _normalize_mutation_evidence_request(project_id, data: dict) -> dict:
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    command = data.get("command") or data.get("mutation_command")
    _normalize_command(command)
    changed_paths = _string_list(data.get("changed_paths") or data.get("target_paths"))
    mutation_threshold = data.get("mutation_threshold")
    if mutation_threshold not in (None, ""):
        try:
            mutation_threshold = float(mutation_threshold)
        except (TypeError, ValueError) as exc:
            raise ValueError("mutation_threshold must be a number") from exc
    artifacts = _mutation_artifacts(data)
    metadata = {
        **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
        "mutation": True,
        "runner": (data.get("runner") or "mutation").strip(),
        "changed_paths": changed_paths,
        "mutation_threshold": mutation_threshold,
        "artifact_defaults": not bool(data.get("artifacts")),
    }

    out = {
        "target_type": target_fields["target_type"],
        "target_id": target_fields["target_id"],
        "check_type": (data.get("check_type") or "mutation").strip() or "mutation",
        "command": command,
        "tool_name": (data.get("tool_name") or metadata["runner"]).strip(),
        "summary": (data.get("summary") or "Mutation testing evidence").strip(),
        "metadata": metadata,
        "artifacts": artifacts,
        "changed_paths": changed_paths,
    }
    if mutation_threshold is not None:
        out["mutation_threshold"] = mutation_threshold
    for key in ("cwd", "timeout_seconds", "sandbox"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    return out


def _mutation_artifacts(data: dict) -> list[dict]:
    if data.get("artifacts") is not None:
        return _normalize_artifacts(data.get("artifacts"))
    report_path = (data.get("report_path") or "mutation-report.json").strip()
    html_path = (data.get("html_report_path") or "").strip()
    artifacts = [{"kind": "report", "label": "Mutation report", "path": report_path}]
    if html_path:
        artifacts.append({"kind": "report", "label": "Mutation HTML report", "path": html_path})
    return artifacts


def _normalize_property_evidence_request(project_id, data: dict) -> dict:
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    command = data.get("command") or data.get("property_command")
    _normalize_command(command)
    properties = _string_list(data.get("properties") or data.get("property_names"))
    generated_cases = data.get("generated_cases")
    if generated_cases not in (None, ""):
        try:
            generated_cases = int(generated_cases)
        except (TypeError, ValueError) as exc:
            raise ValueError("generated_cases must be an integer") from exc
    artifacts = _property_artifacts(data)
    metadata = {
        **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
        "property": True,
        "runner": (data.get("runner") or "property").strip(),
        "properties": properties,
        "generated_cases": generated_cases,
        "artifact_defaults": not bool(data.get("artifacts")),
    }

    out = {
        "target_type": target_fields["target_type"],
        "target_id": target_fields["target_id"],
        "check_type": (data.get("check_type") or "property").strip() or "property",
        "command": command,
        "tool_name": (data.get("tool_name") or metadata["runner"]).strip(),
        "summary": (data.get("summary") or "Property-based testing evidence").strip(),
        "metadata": metadata,
        "artifacts": artifacts,
        "properties": properties,
    }
    if generated_cases is not None:
        out["generated_cases"] = generated_cases
    for key in ("cwd", "timeout_seconds", "sandbox"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    return out


def _property_artifacts(data: dict) -> list[dict]:
    if data.get("artifacts") is not None:
        return _normalize_artifacts(data.get("artifacts"))
    report_path = (data.get("report_path") or "property-report.json").strip()
    examples_path = (data.get("examples_path") or "").strip()
    artifacts = [{"kind": "report", "label": "Property test report", "path": report_path}]
    if examples_path:
        artifacts.append({"kind": "log", "label": "Generated examples", "path": examples_path})
    return artifacts


def _normalize_llm_review_evidence_request(project_id, data: dict) -> dict:
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    reviewer = (data.get("reviewer") or data.get("tool_name") or "").strip()
    if not reviewer:
        raise ValueError("reviewer is required")
    command = data.get("command")
    findings = data.get("findings")
    project = db.session.get(Project, project_id)
    external_worker_required = bool(
        data.get("external_worker")
        or data.get("external_worker_required")
        or data.get("requires_external_worker")
        or (project and project.execution_mode != "local")
    )
    if command not in (None, ""):
        _normalize_command(command)
    elif findings is None:
        if not external_worker_required:
            raise ValueError("command or findings is required")
    if findings is not None:
        findings = _normalize_llm_findings(findings)

    out = {
        "target_type": target_fields["target_type"],
        "target_id": target_fields["target_id"],
        "reviewer": reviewer,
        "check_type": "llm_review",
        "tool_name": reviewer,
        "summary": (data.get("summary") or f"{reviewer} LLM review").strip(),
    }
    if command not in (None, ""):
        out["command"] = command
    if findings is not None:
        out["findings"] = findings
    if external_worker_required and findings is None and command in (None, ""):
        out["external_worker_required"] = True
    for key in ("model", "prompt_version", "cwd", "timeout_seconds", "sandbox", "report_path"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    if data.get("metadata") is not None:
        if not isinstance(data.get("metadata"), dict):
            raise ValueError("metadata must be an object")
        out["metadata"] = data.get("metadata")
    if data.get("artifacts") is not None:
        out["artifacts"] = _normalize_artifacts(data.get("artifacts"))
    return out


def _external_llm_review_checks(run: EvidenceRun, data: dict) -> list[dict]:
    request_data = run.request_data if isinstance(run.request_data, dict) else {}
    reviewer = (
        data.get("reviewer")
        or request_data.get("reviewer")
        or request_data.get("tool_name")
        or "llm_reviewer"
    )
    reviewer = str(reviewer).strip() or "llm_reviewer"
    model = (data.get("model") or request_data.get("model") or "").strip() or None
    prompt_version = (data.get("prompt_version") or request_data.get("prompt_version") or "").strip() or None

    checks = data.get("checks")
    if checks is None and data.get("findings") is not None:
        checks = [{
            "check_type": "llm_review",
            "tool_name": reviewer,
            "findings": data.get("findings"),
            "output": data.get("output"),
            "artifacts": data.get("artifacts"),
        }]
    if not isinstance(checks, list):
        return checks

    normalized_checks = []
    for check in checks:
        if not isinstance(check, dict):
            normalized_checks.append(check)
            continue
        metadata = check.get("metadata") if isinstance(check.get("metadata"), dict) else {}
        raw_findings = (
            check.get("findings")
            if check.get("findings") is not None
            else metadata.get("findings")
            if metadata.get("findings") is not None
            else data.get("findings")
        )
        findings = _normalize_llm_findings(raw_findings)
        status = (check.get("status") or _llm_review_status(findings, False)).strip()
        artifacts = check.get("artifacts")
        if artifacts is None:
            artifacts = _llm_review_artifacts({**request_data, **data})
        normalized_checks.append({
            **check,
            "check_type": (check.get("check_type") or "llm_review").strip(),
            "status": status,
            "tool_name": (check.get("tool_name") or reviewer).strip(),
            "output": check.get("output") or data.get("output") or _llm_review_output(findings),
            "artifacts": artifacts,
            "metadata": {
                **metadata,
                "llm_review": True,
                "reviewer": reviewer,
                "model": model,
                "prompt_version": prompt_version,
                "findings": findings,
                "finding_counts": dict(Counter(finding["severity"] for finding in findings)),
                "blocking_findings": sum(1 for finding in findings if finding["blocking"]),
                "structured": True,
            },
        })
    return normalized_checks


def _llm_review_artifacts(data: dict) -> list[dict]:
    if data.get("artifacts") is not None:
        return _normalize_artifacts(data.get("artifacts"))
    report_path = (data.get("report_path") or "llm-review.json").strip()
    return [{"kind": "report", "label": "LLM review findings", "path": report_path}]


def _findings_from_output(output: str) -> list[dict]:
    text = (output or "").strip()
    if not text:
        raise ValueError("LLM review command must emit structured JSON findings")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM review command must emit structured JSON findings") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("findings")
    return _normalize_llm_findings(parsed)


def _normalize_llm_findings(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("findings must be a list")
    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("findings entries must be objects")
        claim = (item.get("claim") or item.get("message") or "").strip()
        if not claim:
            raise ValueError(f"findings[{index}].claim is required")
        severity = (item.get("severity") or "warning").strip().lower()
        if severity not in {"info", "low", "medium", "high", "critical", "warning"}:
            raise ValueError(f"findings[{index}].severity is invalid")
        normalized.append({
            "severity": severity,
            "path": (item.get("path") or item.get("file") or "").strip() or None,
            "line": item.get("line"),
            "symbol": (item.get("symbol") or "").strip() or None,
            "claim": claim,
            "evidence": (item.get("evidence") or "").strip() or None,
            "suggested_fix": (item.get("suggested_fix") or "").strip() or None,
            "blocking": bool(item.get("blocking")),
            "confidence": item.get("confidence"),
        })
    return normalized


def _llm_review_status(findings: list[dict], command_failed: bool) -> str:
    if command_failed or any(finding["blocking"] for finding in findings):
        return "failed"
    if findings:
        return "warning"
    return "passed"


def _llm_review_output(findings: list[dict]) -> str:
    if not findings:
        return "LLM review completed with no structured findings."
    return "\n".join(
        f"{finding['severity']}: {finding['claim']}"
        for finding in findings
    )[:12000]


def _normalize_test_adequacy_evidence_request(project_id, data: dict) -> dict:
    target_type = (data.get("target_type") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("target_id is required")
    target_fields = _target_bundle_fields(project_id, target_type, target_id)

    command = data.get("command")
    findings = data.get("findings") if data.get("findings") is not None else data.get("assessments")
    generate_candidates = bool(data.get("generate_candidate_tests", False))
    if command not in (None, ""):
        _normalize_command(command)
    elif findings is None and not generate_candidates:
        raise ValueError("command or findings is required")
    if findings is not None:
        findings = _normalize_test_adequacy_findings(findings)

    out = {
        "target_type": target_fields["target_type"],
        "target_id": target_fields["target_id"],
        "check_type": "test_adequacy",
        "tool_name": (data.get("tool_name") or "test_adequacy").strip(),
        "summary": (data.get("summary") or "Test adequacy evidence").strip(),
    }
    if command not in (None, ""):
        out["command"] = command
    if findings is not None:
        out["findings"] = findings
    for key in ("cwd", "timeout_seconds", "sandbox", "report_path"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    for key in ("generated_test_paths", "acceptance_criteria"):
        if data.get(key) is not None:
            out[key] = _string_list(data.get(key))
    for key in ("generate_candidate_tests", "generated_test_prefix", "generated_test_framework", "write_generated_tests", "overwrite_generated_tests"):
        if data.get(key) not in (None, ""):
            out[key] = data.get(key)
    if data.get("generated_test_bodies") is not None:
        if not isinstance(data.get("generated_test_bodies"), dict):
            raise ValueError("generated_test_bodies must be an object")
        out["generated_test_bodies"] = data.get("generated_test_bodies")
    if data.get("metadata") is not None:
        if not isinstance(data.get("metadata"), dict):
            raise ValueError("metadata must be an object")
        out["metadata"] = data.get("metadata")
    if data.get("artifacts") is not None:
        out["artifacts"] = _normalize_artifacts(data.get("artifacts"))
    return out


def _test_adequacy_artifacts(data: dict) -> list[dict]:
    if data.get("artifacts") is not None:
        return _normalize_artifacts(data.get("artifacts"))
    report_path = (data.get("report_path") or "test-adequacy.json").strip()
    return [{"kind": "report", "label": "Test adequacy report", "path": report_path}]


def _test_adequacy_findings_from_output(output: str) -> list[dict]:
    text = (output or "").strip()
    if not text:
        raise ValueError("Test adequacy command must emit structured JSON findings")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Test adequacy command must emit structured JSON findings") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("findings") if parsed.get("findings") is not None else parsed.get("assessments")
    return _normalize_test_adequacy_findings(parsed)


def _normalize_test_adequacy_findings(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("findings must be a list")
    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("findings entries must be objects")
        claim = (item.get("claim") or item.get("concern") or item.get("message") or "").strip()
        criterion = (item.get("criterion") or item.get("acceptance_criterion") or "").strip()
        if not claim and criterion:
            claim = f"Acceptance criterion coverage: {criterion}"
        if not claim:
            raise ValueError(f"findings[{index}].claim is required")
        severity = (item.get("severity") or ("warning" if item.get("covered") is False else "info")).strip().lower()
        if severity not in {"info", "low", "medium", "high", "critical", "warning"}:
            raise ValueError(f"findings[{index}].severity is invalid")
        covered = bool(item.get("covered")) if item.get("covered") is not None else not bool(item.get("blocking"))
        weakened = bool(item.get("weakened_existing_tests") or item.get("deleted_existing_tests"))
        blocking = bool(item.get("blocking")) or weakened or covered is False and severity in {"high", "critical"}
        normalized.append({
            "severity": severity,
            "criterion": criterion or None,
            "test_path": (item.get("test_path") or item.get("path") or item.get("file") or "").strip() or None,
            "covered": covered,
            "claim": claim,
            "evidence": (item.get("evidence") or "").strip() or None,
            "suggested_fix": (item.get("suggested_fix") or "").strip() or None,
            "blocking": blocking,
            "confidence": item.get("confidence"),
            "weakened_existing_tests": weakened,
        })
    return normalized


def _target_acceptance_criteria(project_id, target_fields: dict) -> list[str]:
    criteria = []
    if target_fields.get("target_type") == "attempt":
        attempt = TicketAttempt.query.filter_by(project_id=project_id, id=target_fields.get("target_id")).first()
        if attempt:
            ticket = db.session.get(Ticket, attempt.ticket_id)
            if ticket:
                criteria.extend(_split_acceptance_criteria(ticket.acceptance_criteria))
    elif target_fields.get("target_type") == "composite_workspace":
        for attempt_id in target_fields.get("selected_attempt_ids") or []:
            attempt = TicketAttempt.query.filter_by(project_id=project_id, id=attempt_id).first()
            if not attempt:
                continue
            ticket = db.session.get(Ticket, attempt.ticket_id)
            if ticket:
                criteria.extend(_split_acceptance_criteria(ticket.acceptance_criteria))
    return list(dict.fromkeys(criteria))


def _split_acceptance_criteria(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    else:
        raw_items = str(value).splitlines()
    criteria = []
    for item in raw_items:
        text = item.strip()
        while text[:1] in {"-", "*"}:
            text = text[1:].strip()
        if text:
            criteria.append(text)
    return criteria


def _candidate_tests_from_criteria(data: dict, criteria: list[str]) -> list[dict]:
    prefix = (data.get("generated_test_prefix") or "tests/generated").strip().strip("/") or "tests/generated"
    framework = (data.get("generated_test_framework") or "pytest").strip() or "pytest"
    candidates = []
    for index, criterion in enumerate(criteria, start=1):
        slug = _criterion_slug(criterion) or f"criterion_{index}"
        path = f"{prefix}/test_{slug}.py"
        candidates.append({
            "criterion": criterion,
            "suggested_path": path,
            "framework": framework,
            "test_name": f"test_{slug}",
            "prompt": f"Create a {framework} test that proves: {criterion}",
        })
    return candidates


def _write_generated_test_files(project: Project, data: dict, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    if project.execution_mode != "local":
        raise ValueError("Writing generated tests requires a local project")
    project_path = os.path.abspath((project.project_path or "").strip())
    if not project_path or not os.path.isdir(project_path):
        raise ValueError("Project project_path must be a readable directory")
    bodies = data.get("generated_test_bodies") if isinstance(data.get("generated_test_bodies"), dict) else {}
    overwrite = bool(data.get("overwrite_generated_tests", False))
    written = []
    for candidate in candidates:
        path = candidate["suggested_path"]
        resolved = _resolve_project_output_path(project_path, path, "generated test path")
        if os.path.exists(resolved) and not overwrite:
            raise ValueError(f"Generated test file already exists: {path}")
        body = bodies.get(path) or bodies.get(candidate["test_name"]) or _generated_test_body(candidate)
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as fh:
            fh.write(body)
            if not body.endswith("\n"):
                fh.write("\n")
        written.append({
            "path": path,
            "framework": candidate["framework"],
            "criterion": candidate["criterion"],
            "test_name": candidate["test_name"],
            "bytes": os.path.getsize(resolved),
        })
    return written


def _resolve_project_output_path(project_path: str, path_value: str, label: str) -> str:
    if not path_value or os.path.isabs(path_value):
        raise ValueError(f"{label} must be a relative path")
    requested = os.path.abspath(os.path.join(project_path, path_value))
    common = os.path.commonpath([project_path, requested])
    if common != project_path:
        raise ValueError(f"{label} must be inside project_path")
    return requested


def _generated_test_body(candidate: dict) -> str:
    criterion = candidate["criterion"]
    test_name = candidate["test_name"]
    return (
        f'"""{criterion}."""\n\n\n'
        f"def {test_name}():\n"
        f"    raise AssertionError({('Generated acceptance test needs project-specific assertions: ' + criterion)!r})\n"
    )


def _criterion_slug(value: str) -> str:
    chars = []
    previous_underscore = False
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
            previous_underscore = False
        elif not previous_underscore:
            chars.append("_")
            previous_underscore = True
    return "".join(chars).strip("_")[:60]


def _test_adequacy_status(findings: list[dict], command_failed: bool) -> str:
    if command_failed or any(finding["blocking"] for finding in findings):
        return "failed"
    if any(not finding["covered"] for finding in findings) or findings:
        return "warning"
    return "passed"


def _test_adequacy_output(findings: list[dict]) -> str:
    if not findings:
        return "Test adequacy review completed with no structured concerns."
    return "\n".join(
        f"{finding['severity']}: {finding['claim']}"
        for finding in findings
    )[:12000]


def _bundle_status_from_checks(checks: list[dict]) -> str:
    statuses = {(check.get("status") or "skipped").strip() for check in checks if isinstance(check, dict)}
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    if statuses and statuses <= {"passed", "skipped"} and "passed" in statuses:
        return "passed"
    return "incomplete"


def _risk_from_external_checks(checks: list[dict]) -> str:
    status = _bundle_status_from_checks(checks)
    if status == "failed":
        return "high"
    if status == "warning":
        return "medium"
    if status == "passed":
        return "low"
    return "unknown"


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("value must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def _optional_int(value: Any, name: str) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _normalize_command(value: Any) -> list[str]:
    if isinstance(value, str):
        command = shlex.split(value)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        command = [item for item in value if item]
    else:
        raise ValueError("command must be a string or list of strings")
    if not command:
        raise ValueError("command is required")
    return command


def _normalize_check_suite_item(item: Any) -> dict:
    if not isinstance(item, dict):
        raise ValueError("check_suites entries must be objects")
    check_type = (item.get("check_type") or "").strip()
    if not check_type:
        raise ValueError("check_suites entries require check_type")
    command = item.get("command")
    _normalize_command(command)
    normalized = {
        "check_type": check_type,
        "command": command,
    }
    if item.get("tool_name"):
        normalized["tool_name"] = str(item.get("tool_name")).strip()
    if item.get("cwd"):
        if not isinstance(item.get("cwd"), str):
            raise ValueError("check_suites cwd must be a string")
        normalized["cwd"] = item.get("cwd")
    if item.get("timeout_seconds") not in (None, ""):
        normalized["timeout_seconds"] = _normalize_timeout(item.get("timeout_seconds"))
    if item.get("artifacts") is not None:
        normalized["artifacts"] = _normalize_artifacts(item.get("artifacts"))
    if item.get("sandbox") is not None:
        normalized["sandbox"] = _normalize_sandbox(item.get("sandbox"))
    return normalized


def _normalize_sandbox(value: Any) -> dict:
    if value in (None, ""):
        return {
            "enabled": True,
            "inherit_env": False,
            "env": {},
        }
    if isinstance(value, bool):
        return {
            "enabled": value,
            "inherit_env": not value,
            "env": {},
        }
    if not isinstance(value, dict):
        raise ValueError("sandbox must be a boolean or object")

    enabled = bool(value.get("enabled", True))
    inherit_env = bool(value.get("inherit_env", False if enabled else True))
    env = value.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("sandbox env must be an object of strings")
    for key in env:
        if not key or key.startswith("TERARCHITECT_") or "SECRET" in key.upper() or "TOKEN" in key.upper() or "PASSWORD" in key.upper():
            raise ValueError("sandbox env contains a reserved or sensitive key")
    return {
        "enabled": enabled,
        "inherit_env": inherit_env,
        "env": dict(env),
    }


def _sandbox_env(sandbox: dict, project_path: str | None, home_dir: str | None) -> dict | None:
    if not sandbox.get("enabled") and sandbox.get("inherit_env") and not sandbox.get("env"):
        return None

    env = dict(os.environ) if sandbox.get("inherit_env") else {}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    if project_path:
        env["TERARCHITECT_PROJECT_PATH"] = project_path
    if home_dir:
        env["HOME"] = home_dir
        env["TMPDIR"] = home_dir
        env["PYTHONPYCACHEPREFIX"] = os.path.join(home_dir, "pycache")
    env["PYTHONNOUSERSITE"] = "1"
    env.update(sandbox.get("env") or {})
    return env


def _normalize_artifacts(value: Any, *, project_path: str | None = None, cwd: str | None = None) -> list[dict]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("artifacts must be a list")

    normalized = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("artifact entries must be objects")
        kind = str(item.get("kind") or "other").strip()
        if kind not in VALID_ARTIFACT_KINDS:
            raise ValueError("artifact kind must be one of: " + ", ".join(sorted(VALID_ARTIFACT_KINDS)))
        label = str(item.get("label") or "").strip()
        url = str(item.get("url") or "").strip()
        path = str(item.get("path") or "").strip()
        if not url and not path:
            raise ValueError("artifact entries require url or path")

        artifact = {"kind": kind}
        if label:
            artifact["label"] = label
        if url:
            artifact["url"] = url
        if path:
            artifact.update(_normalize_artifact_path(path, project_path=project_path, cwd=cwd))
        if isinstance(item.get("exists"), bool):
            artifact["exists"] = item["exists"]
        normalized.append(artifact)
    return normalized


def _normalize_artifact_path(path: str, *, project_path: str | None, cwd: str | None) -> dict:
    if not project_path:
        return {"path": path}

    base = cwd or project_path
    requested = path if os.path.isabs(path) else os.path.join(base, path)
    resolved = os.path.abspath(requested)
    common = os.path.commonpath([project_path, resolved])
    if common != project_path:
        raise ValueError("artifact path must be inside project_path")
    relative_path = os.path.relpath(resolved, project_path)
    return {
        "path": relative_path.replace(os.sep, "/"),
        "exists": os.path.exists(resolved),
    }


def _primary_artifact_ref(artifacts: list[dict] | None, explicit: Any = None) -> str | None:
    explicit_ref = (explicit or "").strip()
    if explicit_ref:
        return explicit_ref
    for artifact in artifacts or []:
        if artifact.get("url"):
            return artifact["url"]
        if artifact.get("path"):
            return artifact["path"]
    return None


def _run_local_command(
    command: list[str],
    cwd: str,
    timeout_seconds: int,
    *,
    project_path: str | None = None,
    sandbox: dict | None = None,
) -> dict:
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    output = ""
    exit_code = None
    timed_out = False
    sandbox = _normalize_sandbox(sandbox)
    sandbox_summary = {
        "enabled": sandbox["enabled"],
        "inherit_env": sandbox["inherit_env"],
        "env_keys": sorted((sandbox.get("env") or {}).keys()),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="terarchitect-evidence-") as temp_home:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=_sandbox_env(sandbox, project_path, temp_home if sandbox["enabled"] else None),
            )
        exit_code = result.returncode
        output = ((result.stdout or "") + (result.stderr or ""))[:12000]
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        output = (
            f"Command timed out after {timeout_seconds} seconds.\n"
            f"{_decode_timeout_output(exc.stdout)}{_decode_timeout_output(exc.stderr)}"
        )[:12000]
    except FileNotFoundError as exc:
        exit_code = 127
        output = str(exc)
    finished = datetime.now(timezone.utc)
    return {
        "started": started,
        "finished": finished,
        "duration": round(time.monotonic() - started_clock, 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output": output,
        "sandbox": sandbox_summary,
    }


def _run_git(args: list[str], cwd: str, timeout_seconds: int) -> dict:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[:12000],
            "stderr": (result.stderr or "")[:12000],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": _decode_timeout_output(exc.stdout)[:12000],
            "stderr": f"Git command timed out after {timeout_seconds} seconds.\n{_decode_timeout_output(exc.stderr)}"[:12000],
        }
    except FileNotFoundError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}


def _parse_name_status(output: str) -> list[dict]:
    files = []
    for line in (output or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        item = {"status": status[:1], "path": path}
        if status.startswith("R") and len(parts) >= 3:
            item["old_path"] = parts[1]
            item["path"] = parts[2]
        files.append(item)
    return files


def _parse_numstat(output: str) -> dict:
    counts = {}
    for line in (output or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        path = parts[-1]
        counts[path] = {
            "additions": _parse_numstat_int(parts[0]),
            "deletions": _parse_numstat_int(parts[1]),
        }
    return counts


def _parse_numstat_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _risk_from_diff(file_count: int, changed_lines: int) -> str:
    if file_count == 0 and changed_lines == 0:
        return "low"
    if file_count > 20 or changed_lines > 1000:
        return "high"
    if file_count > 5 or changed_lines > 250:
        return "medium"
    return "low"


def _normalize_timeout(value: Any) -> int:
    if value in (None, ""):
        return 300
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        raise ValueError("timeout_seconds must be an integer")
    if timeout < 1 or timeout > 1800:
        raise ValueError("timeout_seconds must be between 1 and 1800")
    return timeout


def _resolve_command_cwd(project_path: str, cwd_value: Any) -> str:
    if not cwd_value:
        return project_path
    if not isinstance(cwd_value, str):
        raise ValueError("cwd must be a string")
    requested = os.path.abspath(os.path.join(project_path, cwd_value))
    common = os.path.commonpath([project_path, requested])
    if common != project_path or not os.path.isdir(requested):
        raise ValueError("cwd must be an existing directory inside project_path")
    return requested


def _resolve_rerun_cwd(project_path: str, cwd_value: Any) -> str:
    if not cwd_value:
        return project_path
    if not isinstance(cwd_value, str):
        raise ValueError("Stored evidence cwd is not rerunnable")
    requested = os.path.abspath(cwd_value)
    common = os.path.commonpath([project_path, requested])
    if common != project_path or not os.path.isdir(requested):
        raise ValueError("Stored evidence cwd is outside project_path or no longer exists")
    return requested


def _target_bundle_fields(project_id, target_type: str, target_id: str) -> dict:
    if target_type == "composite_workspace":
        target = CompositeWorkspace.query.filter_by(project_id=project_id, id=target_id).first()
        if not target:
            raise ValueError("Composite Workspace not found")
        return {
            "target_type": target_type,
            "target_id": target_id,
            "base_hash": target.base_root_hash,
            "candidate_hash": target.composed_commit_hash,
            "selected_attempt_ids": target.selected_attempt_ids or [],
            "selected_leaf_hashes": target.selected_leaf_hashes or [],
        }
    if target_type == "ship_run":
        target = ShipRun.query.filter_by(project_id=project_id, id=target_id).first()
        if not target:
            raise ValueError("ShipRun not found")
        return {
            "target_type": target_type,
            "target_id": target_id,
            "base_hash": target.base_main_hash,
            "candidate_hash": target.composed_commit_hash or target.shipped_commit_hash,
        }
    if target_type == "attempt":
        target = TicketAttempt.query.filter_by(project_id=project_id, id=target_id).first()
        if not target:
            raise ValueError("Attempt not found")
        return {
            "target_type": target_type,
            "target_id": target_id,
            "base_hash": target.base_hash,
            "candidate_hash": target.agenthub_commit_hash,
            "selected_attempt_ids": [str(target.id)],
            "selected_leaf_hashes": [target.agenthub_commit_hash] if target.agenthub_commit_hash else [],
        }
    raise ValueError("target_type must be one of: attempt, ship_run, composite_workspace")


def _decode_timeout_output(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _check_status_from_test_status(test_status: str) -> str:
    status = (test_status or "").strip().lower()
    if status == "passed":
        return "passed"
    if status == "failed":
        return "failed"
    return "skipped"


def _bundle_status_from_test_status(test_status: str) -> str:
    status = (test_status or "").strip().lower()
    if status == "passed":
        return "passed"
    if status == "failed":
        return "failed"
    return "incomplete"


def _risk_from_test_status(test_status: str) -> str:
    status = (test_status or "").strip().lower()
    if status == "passed":
        return "low"
    if status == "failed":
        return "high"
    return "unknown"


def _latest_waiver(checks: list[EvidenceCheck]) -> EvidenceCheck | None:
    waivers = [
        check for check in checks
        if check.status == "warning" and (check.check_metadata or {}).get("waiver") is True
    ]
    return waivers[-1] if waivers else None


def _latest_human_approval(checks: list[EvidenceCheck]) -> EvidenceCheck | None:
    approvals = [
        check for check in checks
        if check.status == "passed" and (check.check_metadata or {}).get("approval") is True
    ]
    return approvals[-1] if approvals else None


def _default_repair_title(bundle: EvidenceBundle, failing_checks: list[EvidenceCheck]) -> str:
    findings = _structured_repair_findings(failing_checks)
    if findings:
        first = findings[0]
        check_type = first.get("check_type") or "evidence"
        claim = (first.get("claim") or first.get("criterion") or "structured blocker").strip()
        return f"Repair {check_type} blocker: {claim}"[:255]
    check_names = ", ".join(sorted({check.check_type for check in failing_checks})) or bundle.status
    return f"Repair failed evidence for {bundle.target_type} {str(bundle.target_id)[:8]}: {check_names}"


def _repair_policy(data: dict) -> dict:
    policy = data.get("repair_policy") if isinstance(data.get("repair_policy"), dict) else {}
    auto_dispatch = bool(data.get("auto_dispatch_repair", policy.get("auto_dispatch", False)))
    max_attempts = data.get("max_repair_attempts", policy.get("max_attempts", 1))
    try:
        max_attempts = int(max_attempts)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_repair_attempts must be an integer") from exc
    if max_attempts <= 0:
        raise ValueError("max_repair_attempts must be positive")
    return {
        "auto_dispatch": auto_dispatch,
        "max_attempts": max_attempts,
    }


def _enforce_repair_retry_policy(bundle: EvidenceBundle, repair_policy: dict) -> None:
    if not repair_policy.get("auto_dispatch"):
        return
    existing = [
        check for check in list(bundle.checks or [])
        if check.check_type == "repair" and (check.check_metadata or {}).get("repair_ticket_id")
    ]
    if len(existing) >= repair_policy["max_attempts"]:
        raise ValueError("Repair retry policy exhausted for this evidence bundle")


def _dispatch_repair_ticket(ticket: Ticket, repair_policy: dict) -> dict:
    project = db.session.get(Project, ticket.project_id)
    if not project:
        return {"auto_dispatch": True, "dispatch_status": "skipped", "reason": "Project not found"}
    execution_mode = getattr(project, "execution_mode", None) or "docker"
    if execution_mode == "local" and not (project.project_path or "").strip():
        return {"auto_dispatch": True, "dispatch_status": "skipped", "reason": "Project path is required"}
    if execution_mode != "local" and not (project.github_url or "").strip():
        return {"auto_dispatch": True, "dispatch_status": "skipped", "reason": "GitHub URL is required"}

    ticket.column_id = "queued"
    ticket.intent_status = "ready"
    existing = AgentJob.query.filter(
        AgentJob.ticket_id == ticket.id,
        AgentJob.status.in_(["pending", "running"]),
    ).first()
    if existing:
        return {
            "auto_dispatch": True,
            "dispatch_status": "already_queued",
            "job_id": str(existing.id),
            "max_attempts": repair_policy["max_attempts"],
        }
    job = AgentJob(
        ticket_id=ticket.id,
        project_id=ticket.project_id,
        kind="ticket",
        status="pending",
    )
    db.session.add(job)
    db.session.flush()
    return {
        "auto_dispatch": True,
        "dispatch_status": "enqueued",
        "job_id": str(job.id),
        "max_attempts": repair_policy["max_attempts"],
    }


def _repair_description(bundle: EvidenceBundle, failing_checks: list[EvidenceCheck]) -> str:
    sections = [
        f"Evidence bundle: {bundle.id}",
        f"Target: {bundle.target_type} {bundle.target_id}",
        f"Status: {bundle.status}",
        f"Risk: {bundle.risk_level}",
    ]
    if bundle.summary:
        sections.append(f"Summary: {bundle.summary}")
    if bundle.base_hash or bundle.candidate_hash:
        sections.append(f"Base: {bundle.base_hash or 'unknown'}")
        sections.append(f"Candidate: {bundle.candidate_hash or 'unknown'}")
    if failing_checks:
        sections.append("\nFailing checks:")
    for check in failing_checks:
        output = (check.output or "").strip()
        if len(output) > 2000:
            output = output[:2000] + "\n... (truncated)"
        artifact = f"\nArtifact: {check.artifact_url}" if check.artifact_url else ""
        command = f"\nCommand: {check.command}" if check.command else ""
        sections.append(
            f"- {check.check_type} via {check.tool_name or 'manual'} ({check.status})"
            f"{command}{artifact}\n{output or '(no output recorded)'}"
        )
        findings = _structured_repair_findings([check])
        if findings:
            sections.append("Structured blockers:")
            for finding in findings[:10]:
                details = [
                    f"  - {finding.get('severity') or 'unknown'}: {finding.get('claim') or finding.get('criterion')}",
                ]
                if finding.get("path"):
                    line = f":{finding['line']}" if finding.get("line") else ""
                    details.append(f"    Location: {finding['path']}{line}")
                if finding.get("criterion"):
                    details.append(f"    Criterion: {finding['criterion']}")
                if finding.get("evidence"):
                    details.append(f"    Evidence: {finding['evidence']}")
                if finding.get("suggested_fix"):
                    details.append(f"    Suggested fix: {finding['suggested_fix']}")
                sections.append("\n".join(details))
    return "\n".join(sections)


def _repair_acceptance_criteria(bundle: EvidenceBundle, failing_checks: list[EvidenceCheck]) -> str:
    findings = _structured_repair_findings(failing_checks)
    if findings:
        lines = [
            f"Resolve the structured evidence blockers for bundle {bundle.id}:"
        ]
        for finding in findings:
            label = finding.get("claim") or finding.get("criterion") or "Structured finding"
            lines.append(f"- {label}")
            if finding.get("suggested_fix"):
                lines.append(f"  Suggested fix: {finding['suggested_fix']}")
            if finding.get("criterion"):
                lines.append(f"  Acceptance criterion: {finding['criterion']}")
        lines.append("Re-run the failed evidence and ensure it passes or is explicitly waived.")
        return "\n".join(lines)
    check_names = sorted({check.check_type for check in failing_checks})
    if not check_names:
        return f"Evidence bundle {bundle.id} is recollected and no longer {bundle.status}."
    lines = [
        f"Re-run and pass or explicitly waive the failed evidence checks for bundle {bundle.id}:"
    ]
    lines.extend(f"- {name}" for name in check_names)
    return "\n".join(lines)


def _structured_repair_findings(failing_checks: list[EvidenceCheck]) -> list[dict]:
    out = []
    for check in failing_checks:
        metadata = check.check_metadata or {}
        findings = metadata.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            if not finding.get("blocking"):
                continue
            out.append({
                "check_id": str(check.id),
                "check_type": check.check_type,
                "tool_name": check.tool_name,
                "severity": finding.get("severity"),
                "path": finding.get("path") or finding.get("test_path"),
                "line": finding.get("line"),
                "symbol": finding.get("symbol"),
                "criterion": finding.get("criterion"),
                "claim": finding.get("claim"),
                "evidence": finding.get("evidence"),
                "suggested_fix": finding.get("suggested_fix"),
                "confidence": finding.get("confidence"),
            })
    return out


def _post_repair_event(bundle: EvidenceBundle, ticket: Ticket, failing_checks: list[EvidenceCheck]) -> None:
    project = db.session.get(Project, bundle.project_id)
    if not project:
        return
    metadata = {
        "project_id": str(bundle.project_id),
        "evidence_bundle_id": str(bundle.id),
        "target_type": bundle.target_type,
        "target_id": str(bundle.target_id),
        "repair_ticket_id": str(ticket.id),
        "failing_check_ids": [str(check.id) for check in failing_checks],
        "failing_check_types": [check.check_type for check in failing_checks],
    }
    content = _event_content(
        "evidence_repair_created",
        f"Repair ticket created from failed evidence: {ticket.title}",
        metadata,
    )
    for channel in _repair_event_channels(project, bundle):
        _post_event(channel, content)


def _repair_event_channels(project: Project, bundle: EvidenceBundle) -> list[str]:
    channels = {_project_channel(str(project.id))}
    if bundle.target_type == "attempt":
        attempt = TicketAttempt.query.filter_by(project_id=project.id, id=bundle.target_id).first()
        if attempt:
            channels.add(_ticket_channel(str(attempt.ticket_id)))
    elif bundle.target_type == "ship_run":
        run = ShipRun.query.filter_by(project_id=project.id, id=bundle.target_id).first()
        if run:
            channels.add(_wave_channel(project.name, run.wave_num))
    elif bundle.target_type == "composite_workspace":
        ws = CompositeWorkspace.query.filter_by(project_id=project.id, id=bundle.target_id).first()
        if ws:
            for attempt_id in ws.selected_attempt_ids or []:
                attempt = TicketAttempt.query.filter_by(project_id=project.id, id=attempt_id).first()
                if attempt:
                    channels.add(_ticket_channel(str(attempt.ticket_id)))
    return sorted(channels)
