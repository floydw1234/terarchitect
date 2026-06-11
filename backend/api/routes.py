"""
API Routes for Terarchitect
"""
import json
import os
import subprocess
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import UUID

from flask import Blueprint, abort, current_app, jsonify, request
from sqlalchemy import text

from models.db import db, Project, Graph, KanbanBoard, Ticket, Note, ExecutionLog, AgentJob, ShipRun, TicketAttempt, PromotionCandidate, CompositeWorkspace, EvidenceBundle, EvidenceRun
from utils.embedding_client import embed_single
from utils.rag import upsert_embedding, delete_embeddings_for_source
from utils.app_settings import (
    get_value,
    check_execution_readiness,
)

from .services.graph_service import (
    generate_architecture_graph as _generate_architecture_graph,
    update_graph_embeddings as _update_graph_embeddings,
)
from .services.job_service import (
    claim_swarm_job as _claim_swarm_job,
    job_to_response as _job_to_response,
)
from .services.merge_service import (
    ACTIVE_SHIP_RUN_STATUSES as _ACTIVE_SHIP_RUN_STATUSES,
    analyze_wave_dependencies as _analyze_wave_dependencies,
    build_promotion_candidate_snapshot as _build_promotion_candidate_snapshot,
    candidate_attempts as _candidate_attempts,
    candidate_commit_hashes as _candidate_commit_hashes,
    candidate_legacy_wave_num as _candidate_legacy_wave_num,
    compute_waves as _compute_waves,
    lock_project_for_update as _lock_project_for_update,
    promotion_candidate_to_json as _promotion_candidate_to_json,
    ship_run_to_json as _ship_run_to_json,
    validate_promotion_candidate as _validate_promotion_candidate,
)
from .services.notes_service import (
    join_note_link_ids as _join_note_link_ids,
    note_to_json as _note_to_json,
    split_note_link_ids as _split_note_link_ids,
)
from .services.github_service import (
    env_for_gh_user as _env_for_gh_user,
    repo_slug_from_github_url as _repo_slug_from_github_url,
)
from .services.project_service import (
    bootstrap_project_memory as _bootstrap_project_memory,
    normalize_frontier_id as _normalize_frontier_id,
    project_to_json as _project_to_json,
    validate_project_frontier_candidate as _validate_project_frontier_candidate,
)
from .services.agenthub_import_service import (
    AgenthubImportError as _AgenthubImportError,
    import_project_agenthub_root as _import_project_agenthub_root,
)
from .services.ticket_service import (
    dispatch_unblocked_queued as _dispatch_unblocked_queued,
    enqueue_ticket_job as _enqueue_ticket_job,
    resolve_ticket_base_leaf_id as _resolve_ticket_base_leaf_id,
    ticket_to_json as _ticket_to_json,
    validate_ticket_base_leaf as _validate_ticket_base_leaf,
)
from .services.attempt_service import (
    SATISFIED_STATUSES as _SATISFIED_STATUSES,
    attempt_to_json as _attempt_to_json,
    create_attempt as _create_attempt,
    get_accepted_attempt as _get_accepted_attempt,
    get_latest_attempt as _get_latest_attempt,
    transition_attempt as _transition_attempt,
    validate_attempt as _validate_attempt,
)
from .services.attempt_inspection_service import (
    attempt_inspection_json as _attempt_inspection_json,
    inspect_changed_files as _inspect_changed_files,
    inspect_diff as _inspect_diff,
)
from .services.channel_service import (
    ticket_channel as _ticket_channel,
    wave_channel as _wave_channel,
    post_event as _post_event,
    event_content as _event_content,
    fetch_channel_posts as _fetch_channel_posts,
    parse_event_post as _parse_event_post,
)
from .services.workspace_service import (
    workspace_to_json as _workspace_to_json,
    analyze_compatibility as _analyze_compatibility,
)
from .services.evidence_service import (
    add_evidence_check as _add_evidence_check,
    add_evidence_approval as _add_evidence_approval,
    add_evidence_waiver as _add_evidence_waiver,
    cancel_evidence_run as _cancel_evidence_run,
    claim_next_evidence_run as _claim_next_evidence_run,
    complete_external_evidence_run as _complete_external_evidence_run,
    compare_candidate_evidence as _compare_candidate_evidence,
    collect_existing_target_evidence as _collect_existing_target_evidence,
    create_evidence_repair_ticket as _create_evidence_repair_ticket,
    create_evidence_bundle as _create_evidence_bundle,
    create_evidence_run as _create_evidence_run,
    evidence_bundle_to_json as _evidence_bundle_to_json,
    evidence_check_to_json as _evidence_check_to_json,
    evidence_run_to_json as _evidence_run_to_json,
    evaluate_evidence_policy as _evaluate_evidence_policy,
    execute_evidence_run as _execute_evidence_run,
    fail_external_evidence_run as _fail_external_evidence_run,
    normalize_verification_policy as _normalize_verification_policy,
    rerun_failed_evidence_checks as _rerun_failed_evidence_checks,
    run_browser_evidence as _run_browser_evidence,
    run_check_suite_evidence as _run_check_suite_evidence,
    run_command_evidence as _run_command_evidence,
    run_llm_review_evidence as _run_llm_review_evidence,
    run_mutation_evidence as _run_mutation_evidence,
    run_property_evidence as _run_property_evidence,
    run_replay_evidence as _run_replay_evidence,
    run_test_adequacy_evidence as _run_test_adequacy_evidence,
)
from .services.ledger_service import project_ticket_ledger as _project_ticket_ledger
from .services.context_service import build_ticket_context as _build_ticket_context

api_bp = Blueprint("api", __name__)


def _get_project_or_404(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        abort(404)
    return project


def _evidence_gate_response(project, target_type: str, target_id) -> tuple[dict | None, int | None]:
    evaluation = _evaluate_evidence_policy(project, target_type, str(target_id))
    if evaluation["allowed"]:
        return None, None
    return {
        "error": "Required evidence policy has not passed.",
        "target_type": target_type,
        "target_id": str(target_id),
        "evidence_policy": evaluation,
    }, 409


def _ship_doctor_check(name: str, status: str, summary: str, *, detail: str | None = None, next_commands: list[str] | None = None) -> dict:
    payload = {
        "name": name,
        "status": status,
        "summary": summary,
    }
    if detail:
        payload["detail"] = detail
    if next_commands:
        payload["next_commands"] = next_commands
    return payload


def _ship_doctor_report(project) -> dict:
    from sqlalchemy import inspect as _sa_inspect

    project_id = str(project.id)
    checks: list[dict] = []
    next_commands: list[str] = [f"ta ship doctor {project_id}"]

    inspector = _sa_inspect(db.engine)
    tables = set(inspector.get_table_names())
    required_tables = {"ticket_attempts", "promotion_candidates", "ship_runs", "evidence_bundles", "evidence_checks"}
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        checks.append(_ship_doctor_check(
            "db_schema",
            "fail",
            "Ship Room schema is incomplete.",
            detail="Missing tables: " + ", ".join(missing_tables),
        ))
    else:
        checks.append(_ship_doctor_check("db_schema", "pass", "Ship Room tables are present."))

    github_url = (project.github_url or "").strip()
    slug = _repo_slug_from_github_url(github_url) if github_url else None
    if slug:
        checks.append(_ship_doctor_check("project_repo", "pass", f"GitHub target repo resolves to {slug}."))
    else:
        checks.append(_ship_doctor_check(
            "project_repo",
            "warn",
            "Project GitHub target repo is not configured.",
            next_commands=[f"ta project update {project_id} --github-url https://github.com/OWNER/REPO"],
        ))
        next_commands.append(f"ta project update {project_id} --github-url https://github.com/OWNER/REPO")

    frontier = (project.shipped_frontier or "").strip()
    if frontier:
        checks.append(_ship_doctor_check("frontier", "pass", f"Shipped frontier is set to {frontier[:12]}."))
    else:
        checks.append(_ship_doctor_check(
            "frontier",
            "warn",
            "Project shipped frontier is not set.",
            next_commands=[f"ta project show {project_id}"],
        ))
        next_commands.append(f"ta project show {project_id}")

    try:
        result = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_env_for_gh_user(),
        )
        if result.returncode == 0:
            checks.append(_ship_doctor_check("github_auth", "pass", "Backend runtime can authenticate to GitHub."))
        else:
            checks.append(_ship_doctor_check(
                "github_auth",
                "warn",
                "Backend runtime GitHub auth is unavailable.",
                detail=(result.stderr or result.stdout or "").strip() or "gh auth status returned a non-zero exit code.",
                next_commands=[f"ta ship doctor {project_id}"],
            ))
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        checks.append(_ship_doctor_check(
            "github_auth",
            "warn",
            "Backend runtime GitHub auth could not be verified.",
            detail=str(exc),
            next_commands=[f"ta ship doctor {project_id}"],
        ))

    agenthub_url = (os.environ.get("AGENTHUB_URL") or "").rstrip("/")
    if not agenthub_url:
        checks.append(_ship_doctor_check(
            "agenthub",
            "warn",
            "AGENTHUB_URL is not configured in backend runtime.",
            next_commands=[f"ta ship doctor {project_id}"],
        ))
    else:
        try:
            with urllib_request.urlopen(f"{agenthub_url}/health", timeout=5) as resp:
                body = (resp.read() or b"").decode("utf-8", errors="ignore")
            checks.append(_ship_doctor_check("agenthub", "pass", "AgentHub health check succeeded.", detail=body[:200] or None))
        except (urllib_error.URLError, ValueError) as exc:
            checks.append(_ship_doctor_check(
                "agenthub",
                "warn",
                "AgentHub health check could not be completed.",
                detail=str(exc),
                next_commands=[f"ta ship doctor {project_id}"],
            ))

    statuses = {check["status"] for check in checks}
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    deduped_next = []
    for command in next_commands:
        if command not in deduped_next:
            deduped_next.append(command)

    return {
        "project_id": project_id,
        "status": overall,
        "checks": checks,
        "next_commands": deduped_next,
    }


def _ship_error_payload(project, run, *, detail: str, hint: str, phase: str, status_code: int = 502) -> tuple[dict, int]:
    project_id = str(project.id)
    payload = {
        "error": "PR merge failed",
        "detail": detail,
        "hint": hint,
        "phase": phase,
        "request_id": f"ship-run:{run.id}",
        "next_commands": [
            f"ta ship doctor {project_id}",
            f"ta ship run {project_id} {run.id}",
        ],
    }
    return payload, status_code


def _ship_run_evidence_summary(run: ShipRun) -> dict | None:
    bundle = (
        EvidenceBundle.query
        .filter_by(project_id=run.project_id, target_type="ship_run", target_id=run.id)
        .order_by(EvidenceBundle.created_at.desc())
        .first()
    )
    if not bundle:
        return None
    return _evidence_bundle_to_json(bundle)


def _collect_ship_run_evidence(run: ShipRun) -> dict | None:
    try:
        bundle = _collect_existing_target_evidence(run.project_id, {
            "target_type": "ship_run",
            "target_id": str(run.id),
        })
    except Exception as exc:
        current_app.logger.warning("Collecting ship-run evidence failed for %s: %s", run.id, exc)
        return None
    return _evidence_bundle_to_json(bundle)


def _finalize_shipped_run(project, run, *, new_tip: str | None, root_refresh_source: str):
    from datetime import datetime, timezone

    run.status = "shipped"
    run.shipped_commit_hash = new_tip
    run.shipped_at = datetime.now(timezone.utc)

    context = _ship_run_context(run)
    candidate = context["candidate"]
    if candidate is not None:
        candidate.status = "shipped"
        candidate.composed_commit_hash = run.composed_commit_hash
        attempts = _candidate_attempts(candidate)
    else:
        attempts = []
        tickets = Ticket.query.filter_by(project_id=project.id).all()
        all_waves = _compute_waves(tickets)
        for ticket in tickets:
            if all_waves.get(str(ticket.id), 0) != run.wave_num:
                continue
            attempt = _get_accepted_attempt(ticket.id)
            if attempt is not None:
                attempts.append(attempt)

    for attempt in attempts:
        if attempt.status == "shipped":
            continue
        try:
            path = {
                "accepted": ["composed", "release_pr_open", "shipped"],
                "composed": ["release_pr_open", "shipped"],
                "release_pr_open": ["shipped"],
            }
            for next_status in path.get(attempt.status, []):
                _transition_attempt(attempt, next_status, reason=f"ship run {run.id} shipped")
        except ValueError:
            current_app.logger.warning(
                "Could not transition attempt %s (status=%s) to shipped",
                attempt.id, attempt.status,
            )
    db.session.commit()

    if new_tip:
        try:
            _apply_root_refresh(project, new_tip, source=root_refresh_source)
        except Exception as exc:
            current_app.logger.warning("Root refresh after ship failed: %s", exc)

    wave_num = context["wave_num"]
    if run.release_pr_number:
        _post_event(
            _wave_channel(project.name, wave_num),
            _event_content(
                "release_pr_merged",
                f"Release PR #{run.release_pr_number} merged" + (f" at {new_tip[:12]}" if new_tip else ""),
                {
                    "wave_num": wave_num,
                    "ship_run_id": str(run.id),
                    "promotion_candidate_id": str(candidate.id) if candidate else None,
                    "release_pr_number": run.release_pr_number,
                    "release_pr_url": run.release_pr_url,
                    "shipped_commit_hash": new_tip,
                },
            ),
        )
    _post_event(
        _wave_channel(project.name, wave_num),
        _event_content(
            "wave_shipped",
            f"Wave {wave_num} shipped" + (f" at {new_tip[:12]}" if new_tip else ""),
            {
                "wave_num": wave_num,
                "ship_run_id": str(run.id),
                "promotion_candidate_id": str(candidate.id) if candidate else None,
                "shipped_commit_hash": new_tip,
            },
        ),
    )

    evidence_summary = _collect_ship_run_evidence(run)
    payload = _ship_run_detail_payload(run)
    payload["evidence_summary"] = evidence_summary
    return jsonify(payload)


# Worker-facing route prefixes are already protected by _require_worker_auth (Bearer TERARCHITECT_WORKER_API_KEY).
# All other routes are protected by _require_ui_auth (Bearer TERARCHITECT_UI_API_KEY) when that key is set.
_WORKER_ROUTE_PREFIXES = ("/worker/", "/rag/")


@api_bp.before_request
def _ui_auth_check():
    """Gate all non-worker UI routes when TERARCHITECT_UI_API_KEY is set."""
    path = request.path  # e.g. /api/projects/...
    # Strip the blueprint prefix (/api) for comparison
    suffix = path[4:] if path.startswith("/api") else path
    if any(suffix.startswith(p) for p in _WORKER_ROUTE_PREFIXES):
        return  # Worker routes handle their own auth
    # Also pass through requests carrying a valid worker token.
    # Some worker-facing endpoints live under /projects/... (complete, logs, cancel-requested,
    # worker-context) and are not covered by the prefix check above.
    worker_token = (get_value("TERARCHITECT_WORKER_API_KEY") or "").strip()
    if worker_token:
        auth_header = request.headers.get("Authorization") or ""
        if auth_header.startswith("Bearer ") and auth_header[7:].strip() == worker_token:
            return  # Valid worker token — skip UI auth
    err, status = _require_ui_auth()
    if err is not None:
        return err, status

# Worker-facing API: auth via Bearer token. Set TERARCHITECT_WORKER_API_KEY in the backend env to require auth; if unset, no auth (dev).
def _require_worker_auth():
    """Return (None, None) if authorized, else (response, status_code) to return."""
    token = (get_value("TERARCHITECT_WORKER_API_KEY") or "").strip()
    if not token:
        return None, None  # No key configured: allow (dev)
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Missing or invalid Authorization header (expected Bearer <token>)"}), 401
    if auth[7:].strip() != token:
        return jsonify({"error": "Invalid worker API token"}), 401
    return None, None


# UI-facing API: optional auth via Bearer token. Set TERARCHITECT_UI_API_KEY (env) to require auth; if unset, no auth (local dev).
def _require_ui_auth():
    """Return (None, None) if authorized, else (response, status_code) to return.
    Keyed off TERARCHITECT_UI_API_KEY env var only (not DB settings, to avoid a bootstrap chicken-and-egg problem).
    When the key is not set the check is skipped, preserving the local-dev experience."""
    token = (os.environ.get("TERARCHITECT_UI_API_KEY") or "").strip()
    if not token:
        return None, None
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Missing or invalid Authorization header"}), 401
    if auth[7:].strip() != token:
        return jsonify({"error": "Invalid UI API token"}), 401
    return None, None


# Cancel requested by ticket_id is now stored in agent_jobs.cancel_requested column (DB-backed, process-safe).


def _read_local_git_tip(path: str) -> str | None:
    """Return the current HEAD commit hash for the main/master branch at path. Returns None on any error."""
    if not path or not os.path.isdir(path):
        return None
    for branch in ("main", "master"):
        try:
            r = subprocess.run(
                ["git", "rev-parse", branch],
                cwd=path, capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                h = r.stdout.strip()
                if h and len(h) >= 7:
                    return h
        except Exception:
            pass
    return None


def _fetch_github_default_branch_tip(github_url: str) -> str | None:
    """Fetch the default branch (main/master) tip SHA from GitHub via gh CLI.
    Returns None if gh is unavailable, not authenticated, or URL is not parseable."""
    slug = _repo_slug_from_github_url(github_url)
    if not slug:
        return None
    for branch in ("main", "master"):
        try:
            r = subprocess.run(
                ["gh", "api", f"repos/{slug}/git/refs/heads/{branch}"],
                capture_output=True, text=True, timeout=10, env=_env_for_gh_user(),
            )
            if r.returncode == 0:
                data = json.loads(r.stdout or "{}")
                sha = (data.get("object") or {}).get("sha") or None
                if sha:
                    return sha
        except Exception:
            pass
    return None


def _apply_root_refresh(project, new_hash: str, source: str = "wave_merge") -> None:
    """Update shipped_frontier and re-dispatch any newly unblocked queued tickets.

    `shipped_frontier` is the canonical DAG-native shipped base even while the
    live ship flow still uses legacy wave-triggered refresh paths.
    """
    from datetime import datetime, timezone
    project.shipped_frontier = new_hash
    project.shipped_frontier_updated_at = datetime.now(timezone.utc)
    db.session.commit()
    current_app.logger.info(
        "Root refresh project=%s frontier=%s source=%s", project.id, new_hash[:12], source
    )
    try:
        _dispatch_unblocked_queued(project.id)
    except Exception as exc:
        current_app.logger.warning("Root refresh dispatch failed: %s", exc)


@api_bp.route("/projects", methods=["GET", "POST"])
def projects():
    """List all projects or create a new one."""
    if request.method == "GET":
        projects = Project.query.all()
        return jsonify([_project_to_json(p) for p in projects])

    if request.method == "POST":
        data = request.json or {}
        if not data.get("name"):
            return jsonify({"error": "name is required"}), 400
        project_path_val = (data.get("project_path") or "").strip() or None
        accepted_frontier_id = _normalize_frontier_id(data.get("accepted_frontier_id"))
        project = Project(
            name=data.get("name"),
            description=data.get("description"),
            github_url=data.get("github_url"),
            execution_mode="local" if (data.get("execution_mode") or "").strip().lower() == "local" else "docker",
            git_mode="swarm",
            project_path=project_path_val,
            accepted_frontier_id=accepted_frontier_id,
        )
        if accepted_frontier_id is not None:
            valid, error = _validate_project_frontier_candidate(project, accepted_frontier_id)
            if not valid:
                return jsonify({"error": error}), 400
        db.session.add(project)
        db.session.flush()  # assigns project.id from the DB before it's used below
        graph = Graph(project_id=project.id)
        default_columns = [
            {"id": "backlog", "title": "Backlog", "order": 0},
            {"id": "queued", "title": "Queued", "order": 1},
            {"id": "in_progress", "title": "In Progress", "order": 2},
            {"id": "done", "title": "Done", "order": 3},
        ]
        kanban_board = KanbanBoard(project_id=project.id, columns=default_columns)
        db.session.add(graph)
        db.session.add(kanban_board)
        db.session.commit()

        # Create default "Project setup" ticket(s) from config only for new projects (not existing repos)
        is_existing_repo = data.get("is_existing_repo") is True
        if not is_existing_repo:
            _config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
            _default_tickets_path = os.path.join(_config_dir, "default_tickets.json")
            if os.path.isfile(_default_tickets_path):
                try:
                    with open(_default_tickets_path, encoding="utf-8") as f:
                        default_tickets = json.load(f)
                    if isinstance(default_tickets, list):
                        for t in default_tickets:
                            ticket = Ticket(
                                project_id=project.id,
                                column_id="backlog",
                                title=t.get("title", "Untitled"),
                                description=t.get("description"),
                                associated_node_ids=t.get("associated_node_ids", []),
                                associated_edge_ids=t.get("associated_edge_ids", []),
                                priority=t.get("priority", "medium"),
                                status=t.get("status", "todo"),
                            )
                            db.session.add(ticket)
                        db.session.commit()
                except (json.JSONDecodeError, OSError) as e:
                    current_app.logger.warning("Could not create default tickets: %s", e)

        # Bootstrap project memory so agent retrieve has at least one doc (avoids "No facts available")
        _bootstrap_project_memory(project)

        response = {
            **_project_to_json(project),
            "created_at": project.created_at.isoformat(),
            "frontier_warning": None,
        }
        if not project.accepted_frontier_id:
            response["frontier_warning"] = (
                "Project has no canonical AgentHub frontier configured. "
                "Set accepted_frontier_id explicitly on create/import or via PUT /api/projects/{id} "
                "before tickets start defaulting to a DAG frontier."
            )
        return jsonify(response), 201


@api_bp.route("/projects/<uuid:project_id>", methods=["GET", "PUT", "DELETE"])
def project_detail(project_id):
    """Get, update, or delete a project."""
    project = _get_project_or_404(project_id)

    if request.method == "GET":
        return jsonify(_project_to_json(project))

    if request.method == "PUT":
        data = request.json
        project.name = data.get("name", project.name)
        project.description = data.get("description", project.description)
        project.github_url = data.get("github_url", project.github_url)
        if "execution_mode" in data:
            project.execution_mode = "local" if (data.get("execution_mode") or "").strip().lower() == "local" else "docker"
        if "git_mode" in data:
            project.git_mode = "swarm"
        if "project_path" in data:
            project.project_path = data.get("project_path") or None
        if "accepted_frontier_id" in data:
            accepted_frontier_id = _normalize_frontier_id(data.get("accepted_frontier_id"))
            if accepted_frontier_id is not None:
                valid, error = _validate_project_frontier_candidate(project, accepted_frontier_id)
                if not valid:
                    return jsonify({"error": error}), 400
            project.accepted_frontier_id = accepted_frontier_id
        db.session.commit()
        return jsonify(_project_to_json(project))

    if request.method == "DELETE":
        data = request.json or {}
        confirm_name = (data.get("confirm_name") or "").strip()
        if confirm_name != project.name:
            return jsonify({
                "error": "Name does not match. Send confirm_name equal to the project name to confirm deletion.",
            }), 400
        base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
        if base_save_dir:
            try:
                from utils.memory import remove_project_memory
                remove_project_memory(project.id, base_save_dir)
            except Exception as e:
                current_app.logger.warning("Failed to remove project memory for %s: %s", project.id, e)
        # Delete RAG embeddings via raw SQL so the ORM never SELECTs the embedding column (pgvector
        # OID 16397 is unknown to SQLAlchemy's ARRAY(Float)); then delete project.
        db.session.execute(text("DELETE FROM rag_embeddings WHERE project_id = :pid"), {"pid": project.id})
        db.session.delete(project)
        db.session.commit()
        return jsonify({"message": "Project deleted"})


@api_bp.route("/projects/<uuid:project_id>/import-agenthub-root", methods=["POST"])
def project_import_agenthub_root(project_id):
    """Explicitly import a local repo into AgentHub and set accepted_frontier_id."""
    project = _get_project_or_404(project_id)
    data = request.json or {}
    try:
        import_result = _import_project_agenthub_root(project, path_override=data.get("path"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except _AgenthubImportError as exc:
        return jsonify({"error": str(exc)}), 422
    return jsonify({
        "project": _project_to_json(project),
        "import_result": import_result,
    })


@api_bp.route("/projects/<uuid:project_id>/frontier", methods=["POST"])
def project_frontier(project_id):
    """Set the shipped_frontier for a project.
    Body: { "hash": "<commit>", "source": "manual" }
    Also accepts source "local_git" to auto-read from project_path.
    """
    project = _lock_project_for_update(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    data = request.json or {}
    source = (data.get("source") or "manual").strip()
    new_hash = (data.get("hash") or "").strip()

    if source == "local_git":
        # Try local path first, then fall back to GitHub URL
        path = project.project_path or ""
        new_hash = _read_local_git_tip(path) or ""
        if not new_hash and project.github_url:
            new_hash = _fetch_github_default_branch_tip(project.github_url) or ""
        if not new_hash:
            return jsonify({"error": "Could not determine tip: no readable project_path and no accessible GitHub URL"}), 422

    if not new_hash:
        return jsonify({"error": "hash is required"}), 400

    _apply_root_refresh(project, new_hash, source=source)
    return jsonify(_project_to_json(project))


# ---------------------------------------------------------------------------
# Composite Workspace — Phase 9
# Gated behind ENABLE_COMPOSITE_WORKSPACE=1 (default off — lab-grade feature).
# Remove this gate when Phase 14 (Verification Engine) is complete.
# ---------------------------------------------------------------------------

def _workspace_enabled() -> bool:
    return os.environ.get("ENABLE_COMPOSITE_WORKSPACE", "0").strip() in ("1", "true", "yes")


def _require_workspace_enabled():
    """Return (None, None) if workspace feature is enabled, else an error response."""
    if not _workspace_enabled():
        return jsonify({
            "error": "Composite Workspace is not enabled.",
            "hint": "Set ENABLE_COMPOSITE_WORKSPACE=1 to enable this lab-grade feature.",
        }), 503
    return None, None


def _normalize_optional_command(value):
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = value.strip().split()
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value]
    else:
        raise ValueError("preview_command must be a string or list")
    return [part for part in parts if part]


@api_bp.route("/projects/<uuid:project_id>/workspaces", methods=["GET", "POST"])
def workspaces(project_id):
    """List workspaces or create a new draft."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    _get_project_or_404(project_id)

    if request.method == "GET":
        wss = (
            CompositeWorkspace.query
            .filter_by(project_id=project_id)
            .filter(CompositeWorkspace.status != "discarded")
            .order_by(CompositeWorkspace.created_at.desc())
            .all()
        )
        return jsonify([_workspace_to_json(w) for w in wss])

    data = request.json or {}
    attempt_ids = data.get("attempt_ids") or []
    if not attempt_ids:
        return jsonify({"error": "attempt_ids is required"}), 400

    project = db.session.get(Project, project_id)
    frontier = getattr(project, "shipped_frontier", None) or None

    # Collect leaf hashes from selected attempts
    leaf_hashes = []
    for aid in attempt_ids:
        attempt = db.session.get(TicketAttempt, aid)
        if attempt and attempt.agenthub_commit_hash:
            leaf_hashes.append(attempt.agenthub_commit_hash)

    ws = CompositeWorkspace(
        project_id=project_id,
        base_root_hash=frontier,
        selected_attempt_ids=[str(a) for a in attempt_ids],
        selected_leaf_hashes=leaf_hashes,
        status="draft",
        preview_url=(data.get("preview_url") or "").strip() or None,
        preview_status=(data.get("preview_status") or "").strip() or None,
        preview_command=_normalize_optional_command(data.get("preview_command")),
        preview_error=(data.get("preview_error") or "")[:8000] or None,
        created_by=(data.get("created_by") or "").strip() or None,
    )
    db.session.add(ws)
    db.session.commit()
    return jsonify(_workspace_to_json(ws)), 201


@api_bp.route("/projects/<uuid:project_id>/workspaces/analyze", methods=["POST"])
def workspace_analyze(project_id):
    """Compatibility analysis for a proposed set of attempts before composing."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    _get_project_or_404(project_id)
    data = request.json or {}
    attempt_ids = data.get("attempt_ids") or []
    if not attempt_ids:
        return jsonify({"error": "attempt_ids is required"}), 400
    report = _analyze_compatibility(str(project_id), [str(a) for a in attempt_ids])
    return jsonify(report)


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>", methods=["GET"])
def workspace_detail(project_id, workspace_id):
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    include_output = request.args.get("include_test_output", "false").lower() == "true"
    return jsonify(_workspace_to_json(ws, include_test_output=include_output))


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/compose", methods=["POST"])
def workspace_compose(project_id, workspace_id):
    """Queue the workspace for async composition."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("draft", "conflicted", "test_failed"):
        return jsonify({"error": f"Cannot compose from status '{ws.status}'"}), 409
    if not ws.selected_leaf_hashes:
        return jsonify({"error": "No leaves selected — add attempts first"}), 409
    ws.status = "composing"
    db.session.commit()
    current_app.logger.info("Workspace %s queued for composition", workspace_id)
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/bless", methods=["POST"])
def workspace_bless(project_id, workspace_id):
    """Bless this workspace as the preferred candidate state.
    Marks it blessed and updates project.blessed_workspace_id.
    Does NOT imply production or shipping."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("preview_ready", "blessed"):
        return jsonify({"error": f"Cannot bless a workspace in status '{ws.status}' — compose it first"}), 409
    project = db.session.get(Project, project_id)
    if project:
        gate, gate_status = _evidence_gate_response(project, "composite_workspace", workspace_id)
        if gate:
            return jsonify(gate), gate_status
    ws.status = "blessed"
    if project:
        project.blessed_workspace_id = str(workspace_id)
    db.session.commit()
    current_app.logger.info("Workspace %s blessed for project %s", workspace_id, project_id)
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/snapshot", methods=["POST"])
def workspace_snapshot(project_id, workspace_id):
    """Create a Snapshot candidate from this workspace (stub until the Snapshot phase)."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("preview_ready", "blessed"):
        return jsonify({"error": f"Cannot create snapshot from status '{ws.status}'"}), 409
    ws.status = "snapshot_candidate"
    db.session.commit()
    return jsonify({
        **_workspace_to_json(ws),
        "snapshot_note": "Snapshot creation is a stub until first-class Snapshots are implemented.",
    })


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/promote", methods=["POST"])
def workspace_promote(project_id, workspace_id):
    """Promote this workspace to a ShipRun (compatibility export path).
    Creates a ShipRun so the coordinator picks it up and runs the shipper."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("preview_ready", "blessed", "snapshot_candidate"):
        return jsonify({"error": f"Cannot promote a workspace in status '{ws.status}'"}), 409
    project = db.session.get(Project, project_id)
    if project:
        gate, gate_status = _evidence_gate_response(project, "composite_workspace", workspace_id)
        if gate:
            return jsonify(gate), gate_status

    candidate, _snapshot = _ensure_candidate_from_attempt_ids(project, list(ws.selected_attempt_ids or []))
    wave_num = _candidate_legacy_wave_num(candidate)

    # Create a ShipRun — coordinator will dispatch the shipper
    run = ShipRun(
        project_id=str(project_id),
        promotion_candidate_id=str(candidate.id),
        wave_num=wave_num,
        status="queued",
        summary=f"Promoted from Composite Workspace {str(workspace_id)[:8]}",
    )
    db.session.add(run)
    # Keep the workspace at its current status — the ShipRun is the promotion artifact.
    # The workspace remains inspectable (blessed/snapshot_candidate) after promotion.
    db.session.commit()
    current_app.logger.info(
        "Workspace %s promoted to ship run %s (wave %d)", workspace_id, run.id, wave_num
    )
    return jsonify({
        "workspace": _workspace_to_json(ws),
        "ship_run": _ship_run_to_json(run),
        "label": "Promoted for Export",
    })


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/discard", methods=["POST"])
def workspace_discard(project_id, workspace_id):
    """Discard this workspace. Discarded workspaces are hidden from the list."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    ws.status = "discarded"
    # Clear blessed if this was the blessed workspace
    project = db.session.get(Project, project_id)
    if project and str(project.blessed_workspace_id) == str(workspace_id):
        project.blessed_workspace_id = None
    db.session.commit()
    return jsonify(_workspace_to_json(ws))


# ---------------------------------------------------------------------------
# Evidence Bundles — Phase 14
# Storage/query, policy explanation, and bless/promote/ship gate surface.
# ---------------------------------------------------------------------------

@api_bp.route("/projects/<uuid:project_id>/verification-policy", methods=["GET", "PUT"])
def project_verification_policy(project_id):
    project = _get_project_or_404(project_id)
    if request.method == "GET":
        return jsonify(_normalize_verification_policy(project.verification_policy))

    try:
        project.verification_policy = _normalize_verification_policy(request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    db.session.commit()
    return jsonify(project.verification_policy)


@api_bp.route("/projects/<uuid:project_id>/evidence/policy", methods=["GET"])
def evidence_policy(project_id):
    project = _get_project_or_404(project_id)
    target_type = (request.args.get("target_type") or "").strip()
    target_id = (request.args.get("target_id") or "").strip()
    if not target_type or not target_id:
        return jsonify({"error": "target_type and target_id are required"}), 400
    return jsonify(_evaluate_evidence_policy(project, target_type, target_id))


@api_bp.route("/projects/<uuid:project_id>/evidence/collect", methods=["POST"])
def evidence_collect(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _collect_existing_target_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-command", methods=["POST"])
def evidence_run_command(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_command_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-suite", methods=["POST"])
def evidence_run_suite(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_check_suite_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-browser", methods=["POST"])
def evidence_run_browser(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_browser_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-replay", methods=["POST"])
def evidence_run_replay(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_replay_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-mutation", methods=["POST"])
def evidence_run_mutation(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_mutation_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-property", methods=["POST"])
def evidence_run_property(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_property_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-llm-review", methods=["POST"])
def evidence_run_llm_review(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_llm_review_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/run-test-adequacy", methods=["POST"])
def evidence_run_test_adequacy(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _run_test_adequacy_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/runs", methods=["GET", "POST"])
def evidence_runs(project_id):
    _get_project_or_404(project_id)
    if request.method == "GET":
        status = (request.args.get("status") or "").strip()
        target_type = (request.args.get("target_type") or "").strip()
        target_id = (request.args.get("target_id") or "").strip()
        q = EvidenceRun.query.filter_by(project_id=project_id)
        if status:
            q = q.filter_by(status=status)
        if target_type:
            q = q.filter_by(target_type=target_type)
        if target_id:
            q = q.filter_by(target_id=target_id)
        runs = q.order_by(EvidenceRun.created_at.desc()).all()
        return jsonify([_evidence_run_to_json(run) for run in runs])

    try:
        run = _create_evidence_run(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_run_to_json(run)), 202


@api_bp.route("/projects/<uuid:project_id>/evidence/runs/<uuid:run_id>", methods=["GET"])
def evidence_run_detail(project_id, run_id):
    _get_project_or_404(project_id)
    run = EvidenceRun.query.filter_by(project_id=project_id, id=run_id).first_or_404()
    return jsonify(_evidence_run_to_json(run, include_bundle=True))


@api_bp.route("/projects/<uuid:project_id>/evidence/runs/<uuid:run_id>/cancel", methods=["POST"])
def evidence_run_cancel(project_id, run_id):
    _get_project_or_404(project_id)
    run = EvidenceRun.query.filter_by(project_id=project_id, id=run_id).first_or_404()
    try:
        run = _cancel_evidence_run(run)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_run_to_json(run, include_bundle=True))


@api_bp.route("/projects/<uuid:project_id>/evidence/compare", methods=["POST"])
def evidence_compare(project_id):
    _get_project_or_404(project_id)
    try:
        bundle = _compare_candidate_evidence(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence", methods=["GET", "POST"])
def project_evidence(project_id):
    _get_project_or_404(project_id)

    if request.method == "GET":
        target_type = (request.args.get("target_type") or "").strip()
        target_id = (request.args.get("target_id") or "").strip()
        check_type = (request.args.get("check_type") or "").strip()

        q = EvidenceBundle.query.filter_by(project_id=project_id)
        if target_type:
            q = q.filter_by(target_type=target_type)
        if target_id:
            q = q.filter_by(target_id=target_id)
        if check_type:
            q = q.join(EvidenceBundle.checks).filter_by(check_type=check_type)
        bundles = q.order_by(EvidenceBundle.created_at.desc()).all()
        return jsonify([_evidence_bundle_to_json(b) for b in bundles])

    try:
        bundle = _create_evidence_bundle(project_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/<uuid:bundle_id>", methods=["GET"])
def evidence_detail(project_id, bundle_id):
    _get_project_or_404(project_id)
    bundle = EvidenceBundle.query.filter_by(project_id=project_id, id=bundle_id).first_or_404()
    return jsonify(_evidence_bundle_to_json(bundle, include_checks=True))


@api_bp.route("/projects/<uuid:project_id>/evidence/<uuid:bundle_id>/checks", methods=["POST"])
def evidence_checks(project_id, bundle_id):
    _get_project_or_404(project_id)
    bundle = EvidenceBundle.query.filter_by(project_id=project_id, id=bundle_id).first_or_404()
    try:
        check = _add_evidence_check(bundle, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_check_to_json(check)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/<uuid:bundle_id>/waivers", methods=["POST"])
def evidence_waivers(project_id, bundle_id):
    _get_project_or_404(project_id)
    bundle = EvidenceBundle.query.filter_by(project_id=project_id, id=bundle_id).first_or_404()
    try:
        check = _add_evidence_waiver(bundle, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_check_to_json(check)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/<uuid:bundle_id>/approvals", methods=["POST"])
def evidence_approvals(project_id, bundle_id):
    _get_project_or_404(project_id)
    bundle = EvidenceBundle.query.filter_by(project_id=project_id, id=bundle_id).first_or_404()
    try:
        check = _add_evidence_approval(bundle, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_check_to_json(check)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/<uuid:bundle_id>/repair", methods=["POST"])
def evidence_repair(project_id, bundle_id):
    _get_project_or_404(project_id)
    bundle = EvidenceBundle.query.filter_by(project_id=project_id, id=bundle_id).first_or_404()
    try:
        ticket = _create_evidence_repair_ticket(bundle, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_ticket_to_json(ticket)), 201


@api_bp.route("/projects/<uuid:project_id>/evidence/<uuid:bundle_id>/rerun", methods=["POST"])
def evidence_rerun(project_id, bundle_id):
    _get_project_or_404(project_id)
    bundle = EvidenceBundle.query.filter_by(project_id=project_id, id=bundle_id).first_or_404()
    try:
        rerun_bundle = _rerun_failed_evidence_checks(bundle, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_evidence_bundle_to_json(rerun_bundle, include_checks=True)), 201


# ---------------------------------------------------------------------------
# Worker-facing evidence endpoints
# ---------------------------------------------------------------------------

@api_bp.route("/worker/evidence-runs/next", methods=["POST"])
def worker_evidence_run_next():
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    project_id = data.get("project_id")
    if project_id:
        _get_project_or_404(project_id)
    run = _claim_next_evidence_run(project_id)
    if not run:
        return jsonify({"run": None})
    return jsonify({"run": _evidence_run_to_json(run)})


@api_bp.route("/worker/evidence-runs/<uuid:run_id>/execute", methods=["POST"])
def worker_evidence_run_execute(run_id):
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = EvidenceRun.query.filter_by(id=run_id).first_or_404()
    try:
        run = _execute_evidence_run(run)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run": _evidence_run_to_json(run, include_bundle=True)})


@api_bp.route("/worker/evidence-runs/<uuid:run_id>/complete", methods=["POST"])
def worker_evidence_run_complete(run_id):
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = EvidenceRun.query.filter_by(id=run_id).first_or_404()
    try:
        run = _complete_external_evidence_run(run, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run": _evidence_run_to_json(run, include_bundle=True)})


@api_bp.route("/worker/evidence-runs/<uuid:run_id>/fail", methods=["POST"])
def worker_evidence_run_fail(run_id):
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = EvidenceRun.query.filter_by(id=run_id).first_or_404()
    try:
        run = _fail_external_evidence_run(run, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run": _evidence_run_to_json(run, include_bundle=True)})


# ---------------------------------------------------------------------------
# Worker-facing workspace endpoints (coordinator + composer)
# ---------------------------------------------------------------------------

@api_bp.route("/worker/workspaces/next", methods=["POST"])
def worker_workspace_next():
    """Coordinator claims the next workspace queued for composition."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status

    ws = (
        CompositeWorkspace.query
        .filter_by(status="composing")
        .order_by(CompositeWorkspace.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not ws:
        return "", 204

    project = db.session.get(Project, ws.project_id)
    return jsonify({
        "workspace": _workspace_to_json(ws),
        "project": {
            "id": str(project.id) if project else None,
            "name": project.name if project else "",
            "project_path": project.project_path if project else None,
            "github_url": project.github_url if project else None,
        },
        "leaf_hashes": ws.selected_leaf_hashes or [],
    }), 200


@api_bp.route("/worker/workspaces/<uuid:workspace_id>", methods=["GET"])
def worker_workspace_get(workspace_id):
    """Fetch workspace data for a pre-claimed workspace."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(id=workspace_id).first_or_404()
    project = db.session.get(Project, ws.project_id)
    return jsonify({
        "workspace": _workspace_to_json(ws),
        "project": {
            "id": str(project.id) if project else None,
            "name": project.name if project else "",
            "project_path": project.project_path if project else None,
        },
        "leaf_hashes": ws.selected_leaf_hashes or [],
    })


@api_bp.route("/worker/workspaces/<uuid:workspace_id>/composed", methods=["POST"])
def worker_workspace_composed(workspace_id):
    """Composer reports successful composition."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(id=workspace_id).first_or_404()
    data = request.json or {}
    ws.status = "preview_ready"
    ws.composed_commit_hash = (data.get("composed_commit_hash") or "").strip() or None
    ws.test_status = (data.get("test_status") or "").strip() or None
    ws.test_output = (data.get("test_output") or "")[:8000] or None
    ws.changed_files = data.get("changed_files") or []
    ws.preview_url = (data.get("preview_url") or ws.preview_url or "").strip() or None
    ws.preview_status = (data.get("preview_status") or ("ready" if ws.preview_url else "")).strip() or None
    ws.preview_command = _normalize_optional_command(data.get("preview_command")) or ws.preview_command or []
    ws.preview_error = (data.get("preview_error") or "")[:8000] or None
    db.session.commit()
    current_app.logger.info("Workspace %s composed: hash=%s tests=%s files=%d",
                            workspace_id, ws.composed_commit_hash, ws.test_status,
                            len(ws.changed_files or []))
    if ws.created_by == "dependency_base_composer" and ws.composed_commit_hash:
        try:
            _dispatch_unblocked_queued(ws.project_id)
        except Exception as exc:
            current_app.logger.warning("Dependency base dispatch failed for workspace %s: %s", workspace_id, exc)
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/worker/workspaces/<uuid:workspace_id>/fail", methods=["POST"])
def worker_workspace_fail(workspace_id):
    """Composer reports failure."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(id=workspace_id).first_or_404()
    data = request.json or {}
    failure_type = (data.get("failure_type") or "").strip()
    ws.status = "test_failed" if failure_type == "test_failed" else "conflicted"
    ws.conflict_summary = (data.get("error") or "")[:4000]
    if data.get("test_status"):
        ws.test_status = data["test_status"]
    if data.get("test_output"):
        ws.test_output = data["test_output"][:8000]
    if data.get("composed_commit_hash"):
        ws.composed_commit_hash = data["composed_commit_hash"]
    db.session.commit()
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/worker/workspaces/reset-stale", methods=["POST"])
def worker_workspace_reset_stale():
    """Reset workspace composer runs stuck in 'composing' after a coordinator restart."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    try:
        max_age = int(data.get("max_age_seconds", 1800))
    except (TypeError, ValueError):
        max_age = 1800
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    stale = CompositeWorkspace.query.filter(
        CompositeWorkspace.status == "composing",
        CompositeWorkspace.updated_at < cutoff,
    ).all()
    count = len(stale)
    for ws in stale:
        ws.status = "draft"
        ws.conflict_summary = f"Reset by coordinator after {max_age}s stale timeout."
    db.session.commit()
    return jsonify({"reset": count, "max_age_seconds": max_age})


@api_bp.route("/projects/<uuid:project_id>/debug", methods=["GET"])
def project_debug(project_id):
    """Debug/observability endpoint. Returns frontier, accepted attempts by wave,
    open ship runs, and stale attempt count. Useful for diagnosing stuck states."""
    project = _get_project_or_404(project_id)
    frontier = getattr(project, "shipped_frontier", None) or None

    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)

    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project_id)
        .order_by(TicketAttempt.wave_num, TicketAttempt.created_at, TicketAttempt.attempt_num)
        .all()
    )

    by_wave: dict = {}
    pending_leaves = []
    stale_attempts = []
    stale_count = 0
    for a in attempts:
        w = a.wave_num
        is_stale = (a.base_hash != frontier) if (frontier and a.base_hash) else None
        attempt_record = {
            "id": str(a.id),
            "ticket_id": str(a.ticket_id),
            "attempt_num": a.attempt_num,
            "status": a.status,
            "agenthub_commit_hash": a.agenthub_commit_hash,
            "short_hash": (a.agenthub_commit_hash or "")[:12] or None,
            "base_hash": a.base_hash,
            "base_short_hash": (a.base_hash or "")[:12] or None,
            "stale": is_stale,
            "validation_error": a.validation_error,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        if a.status in _SATISFIED_STATUSES:
            by_wave.setdefault(str(w), []).append(attempt_record)
        if a.agenthub_commit_hash and a.status not in ("shipped", "rejected", "superseded", "failed"):
            pending_leaves.append(attempt_record)
        if is_stale:
            stale_count += 1
            stale_attempts.append(attempt_record)

    open_runs = ShipRun.query.filter_by(project_id=project_id).filter(
        ShipRun.status.in_(["queued", "composing", "running", "ready_to_ship", "shipping"])
    ).all()

    jobs = AgentJob.query.filter_by(project_id=project_id).filter(
        AgentJob.status.in_(["pending", "running"])
    ).order_by(AgentJob.created_at.asc()).all()

    wave_summary: dict = {}
    for ticket in tickets:
        w = waves.get(str(ticket.id), 0)
        summary = wave_summary.setdefault(str(w), {
            "wave_num": w,
            "ticket_count": 0,
            "accepted_count": 0,
            "stale_count": 0,
            "tickets": [],
        })
        accepted_attempt = _get_accepted_attempt(ticket.id)
        accepted_stale = (
            accepted_attempt.base_hash != frontier
            if accepted_attempt and frontier and accepted_attempt.base_hash
            else None
        )
        summary["ticket_count"] += 1
        if accepted_attempt:
            summary["accepted_count"] += 1
        if accepted_stale:
            summary["stale_count"] += 1
        summary["tickets"].append({
            "id": str(ticket.id),
            "title": ticket.title,
            "column_id": ticket.column_id,
            "intent_status": ticket.intent_status,
            "accepted_attempt_id": str(accepted_attempt.id) if accepted_attempt else None,
            "accepted_attempt_stale": accepted_stale,
        })

    return jsonify({
        "project_id": str(project_id),
        "shipped_frontier": frontier,
        "shipped_frontier_updated_at": (
            project.shipped_frontier_updated_at.isoformat()
            if project.shipped_frontier_updated_at else None
        ),
        "wave_summary": [
            wave_summary[key] for key in sorted(wave_summary.keys(), key=lambda value: int(value))
        ],
        "accepted_attempts_by_wave": by_wave,
        "pending_leaves": pending_leaves,
        "stale_attempt_count": stale_count,
        "stale_attempts": stale_attempts,
        "open_ship_runs": [_ship_run_to_json(r) for r in open_runs],
        "active_jobs": [
            {
                "id": str(job.id),
                "ticket_id": str(job.ticket_id),
                "kind": job.kind,
                "status": job.status,
                "cancel_requested": job.cancel_requested,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            }
            for job in jobs
        ],
        "ticket_count": len(tickets),
    })


@api_bp.route("/projects/<uuid:project_id>/graph", methods=["GET", "PUT"])
def graph(project_id):
    """Get or update the project's graph."""
    graph = Graph.query.filter_by(project_id=project_id).first_or_404()

    if request.method == "GET":
        return jsonify({
            "id": str(graph.id),
            "project_id": str(graph.project_id),
            "nodes": graph.nodes if graph.nodes is not None else [],
            "edges": graph.edges if graph.edges is not None else [],
            "version": graph.version,
        })

    if request.method == "PUT":
        data = request.json
        if "nodes" in data:
            graph.nodes = data["nodes"] if data["nodes"] is not None else []
        if "edges" in data:
            graph.edges = data["edges"] if data["edges"] is not None else []
        graph.version = graph.version + 1
        db.session.commit()

        # RAG: replace node/edge embeddings for this project
        _update_graph_embeddings(project_id, graph)

        return jsonify({"version": graph.version})


@api_bp.route("/projects/<uuid:project_id>/graph/generate", methods=["POST"])
def graph_generate(project_id):
    """Clone the project's GitHub repo and use the LLM to generate an architecture graph.
    Only works when the graph is empty (no nodes). Returns generated nodes and edges,
    and writes them directly to the graph."""
    return _generate_architecture_graph(project_id)


@api_bp.route("/projects/<uuid:project_id>/kanban", methods=["GET", "PUT"])
def kanban(project_id):
    """Get or update the project's kanban board."""
    kanban = KanbanBoard.query.filter_by(project_id=project_id).first_or_404()

    if request.method == "GET":
        return jsonify({
            "id": str(kanban.id),
            "project_id": str(kanban.project_id),
            "columns": kanban.columns,
        })

    if request.method == "PUT":
        data = request.json
        if "columns" in data:
            kanban.columns = data["columns"] if data["columns"] is not None else []
        db.session.commit()
        return jsonify({"columns": kanban.columns})


@api_bp.route("/projects/<uuid:project_id>/tickets", methods=["GET", "POST"])
def tickets(project_id):
    """List tickets or create a new one."""
    if request.method == "GET":
        tickets = Ticket.query.filter_by(project_id=project_id).all()
        return jsonify([_ticket_to_json(t) for t in tickets])

    if request.method == "POST":
        data = request.json or {}
        if not data.get("title") or not data.get("column_id"):
            return jsonify({"error": "title and column_id are required"}), 400
        project = db.session.get(Project, project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404
        base_leaf_id = _resolve_ticket_base_leaf_id(
            project,
            data.get("base_leaf_id"),
            explicit_provided="base_leaf_id" in data,
        )
        valid, error = _validate_ticket_base_leaf(project, base_leaf_id)
        if not valid:
            return jsonify({"error": error}), 400
        ticket = Ticket(
            project_id=project_id,
            column_id=data["column_id"],
            title=data["title"],
            description=data.get("description"),
            associated_node_ids=data.get("associated_node_ids", []),
            associated_edge_ids=data.get("associated_edge_ids", []),
            priority=data.get("priority", "medium"),
            status=data.get("status", "todo"),
            depends_on_ticket_ids=data.get("depends_on_ticket_ids", []),
            intent_status=data.get("intent_status", "ready"),
            rationale=data.get("rationale"),
            acceptance_criteria=data.get("acceptance_criteria"),
            constraints=data.get("constraints"),
            value_score=data.get("value_score"),
            risk_level=data.get("risk_level"),
            created_source=data.get("created_source", "manual"),
            base_leaf_id=base_leaf_id,
        )
        db.session.add(ticket)
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        return jsonify(_ticket_to_json(ticket)), 201


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>", methods=["GET", "PATCH", "DELETE"])
def ticket_detail(project_id, ticket_id):
    """Get, update, or delete a single ticket."""
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()

    if request.method == "GET":
        return jsonify(_ticket_to_json(ticket))

    if request.method == "PATCH":
        data = request.json
        moved_to_in_progress = (
            data.get("column_id") == "in_progress" and ticket.column_id != "in_progress"
        )
        if moved_to_in_progress:
            project = db.session.get(Project, project_id)
            if not project:
                return jsonify({"error": "Project not found"}), 404
            valid, error = _validate_ticket_base_leaf(project, getattr(ticket, "base_leaf_id", None))
            if not valid:
                return jsonify({"error": error}), 400
            execution_mode = getattr(project, "execution_mode", None) or "docker"
            if execution_mode == "local":
                if not (project.project_path or "").strip():
                    return jsonify({
                        "error": "Project has execution mode Local; set Project path in project settings before moving a ticket to In Progress.",
                    }), 400
            else:
                if not (project.github_url or "").strip():
                    return jsonify({
                        "error": "Project must have a GitHub URL set before moving a ticket to In Progress.",
                    }), 400
            graph = Graph.query.filter_by(project_id=project_id).first()
            if not graph or not graph.nodes or len(graph.nodes) == 0:
                return jsonify({
                    "error": "Add at least one node to the graph before moving a ticket to In Progress.",
                }), 400
            ready, missing = check_execution_readiness()
            if not ready:
                missing_str = ", ".join(f"{label} ({key})" for key, label in missing)
                return jsonify({
                    "error": f"Cannot run: set these in .env and restart the backend: {missing_str}.",
                }), 400
            dep_ids = ticket.depends_on_ticket_ids or []
            if dep_ids:
                # A dependency is satisfied when it has an accepted attempt
                blocking = [
                    db.session.get(Ticket, d) for d in dep_ids
                    if not TicketAttempt.query.filter_by(ticket_id=d).filter(
                        TicketAttempt.status.in_(_SATISFIED_STATUSES)
                    ).first()
                ]
                blocking = [b for b in blocking if b]
                if blocking:
                    titles = ", ".join(f'"{b.title}"' for b in blocking[:3])
                    suffix = f" (+{len(blocking) - 3} more)" if len(blocking) > 3 else ""
                    return jsonify({
                        "error": f"Blocked by tickets with no accepted attempt: {titles}{suffix}.",
                    }), 400
        if "column_id" in data:
            new_col = data["column_id"]
            _SYSTEM_COLUMNS = {"backlog", "queued", "in_progress", "done"}
            kanban = KanbanBoard.query.filter_by(project_id=project_id).first()
            valid_cols = {c["id"] for c in (kanban.columns or [])} if kanban else set()
            if valid_cols and new_col not in valid_cols and new_col not in _SYSTEM_COLUMNS:
                return jsonify({"error": f"Invalid column_id '{new_col}'"}), 400
            ticket.column_id = new_col
        if "title" in data:
            ticket.title = data["title"]
        if "description" in data:
            ticket.description = data["description"]
        if "priority" in data:
            ticket.priority = data["priority"]
        if "status" in data:
            ticket.status = data["status"]
        if "associated_node_ids" in data:
            ticket.associated_node_ids = data["associated_node_ids"]
        if "associated_edge_ids" in data:
            ticket.associated_edge_ids = data["associated_edge_ids"]
        if "depends_on_ticket_ids" in data:
            ticket.depends_on_ticket_ids = data["depends_on_ticket_ids"]
        if "intent_status" in data:
            ticket.intent_status = data["intent_status"]
        if "rationale" in data:
            ticket.rationale = data["rationale"]
        if "acceptance_criteria" in data:
            ticket.acceptance_criteria = data["acceptance_criteria"]
        if "constraints" in data:
            ticket.constraints = data["constraints"]
        if "value_score" in data:
            ticket.value_score = data["value_score"]
        if "risk_level" in data:
            ticket.risk_level = data["risk_level"]
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        if moved_to_in_progress:
            ticket.intent_status = "active"
            db.session.commit()
            _enqueue_ticket_job(ticket.id)
        if data.get("column_id") == "queued":
            # Manual re-queue — fire retry_requested if the ticket had previous attempts
            has_attempts = TicketAttempt.query.filter_by(ticket_id=ticket.id).first()
            if has_attempts:
                _post_event(
                    _ticket_channel(str(ticket_id)),
                    _event_content("retry_requested", "Ticket manually re-queued"),
                )
        return jsonify(_ticket_to_json(ticket))

    if request.method == "DELETE":
        # Clean up embeddings and any queued/running agent jobs that reference this ticket before delete.
        for c in ticket.comments:
            delete_embeddings_for_source(project_id, "ticket_comment", c.id)
        delete_embeddings_for_source(project_id, "ticket", ticket.id)
        AgentJob.query.filter_by(ticket_id=ticket.id).delete()
        db.session.delete(ticket)
        db.session.commit()
        return jsonify({"message": "Ticket deleted"})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/logs", methods=["GET"])
def ticket_logs(project_id, ticket_id):
    """Get execution logs for a ticket (for debugging)."""
    logs = ExecutionLog.query.filter_by(
        project_id=project_id,
        ticket_id=ticket_id,
    ).order_by(ExecutionLog.created_at.asc()).all()
    payload = []
    for log in logs:
        entry = {
            "id": str(log.id),
            "step": log.step,
            "summary": log.summary,
            "raw_output": log.raw_output,
            "success": log.success,
            "session_id": log.session_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        if log.raw_output:
            try:
                parsed = json.loads(log.raw_output)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                if parsed.get("kind") == "ticket_run_event":
                    entry["event"] = parsed
                elif parsed.get("kind") == "ticket_run_receipt":
                    entry["receipt"] = parsed
        payload.append(entry)
    return jsonify(payload)


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/worker-context", methods=["GET"])
def worker_context(project_id, ticket_id):
    """Phase 1: Worker-facing context. Same shape as build_worker_context(ticket); no project_path. Auth: Bearer token."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    project = _get_project_or_404(project_id)
    try:
        from worker_context import build_worker_context
        context = build_worker_context(ticket)
    except Exception as e:
        current_app.logger.exception("worker_context: build_worker_context failed: %s", e)
        return jsonify({"error": "Failed to load context", "detail": str(e)}), 500
    context.pop("project_path", None)
    context["repo_url"] = project.github_url or ""
    context["project_id"] = str(project_id)
    return jsonify(context)


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/logs", methods=["POST"])
def ticket_logs_append(project_id, ticket_id):
    """Phase 1: Append an execution log entry (worker-facing). Body: session_id, step, summary, raw_output (optional), success (optional, default True). Auth: Bearer."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    step = (data.get("step") or "").strip() or "step"
    summary = (data.get("summary") or "").strip() or ""
    raw_output = data.get("raw_output")
    success = data.get("success", True)
    if not isinstance(success, bool):
        success = bool(success)
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    log_entry = ExecutionLog(
        project_id=project_id,
        ticket_id=ticket_id,
        session_id=session_id,
        step=step[:100],
        summary=summary,
        raw_output=raw_output,
        success=success,
    )
    db.session.add(log_entry)
    db.session.commit()
    return jsonify({"id": str(log_entry.id), "message": "Logged"})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/complete", methods=["POST"])
def ticket_complete(project_id, ticket_id):
    """Mark ticket complete (worker-facing). Body: commit_hash, summary, agent_id. Auth: Bearer."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    if ticket.column_id != "in_progress":
        return jsonify({"error": "Ticket is not in_progress; cannot mark complete"}), 409
    data = request.json or {}
    summary = (data.get("summary") or "").strip() or ""
    commit_hash = (data.get("commit_hash") or "").strip() or None
    base_hash = (data.get("base_hash") or "").strip() or None
    agent_id_val = (data.get("agent_id") or "").strip() or None

    ticket.status = "completed"
    ticket.column_id = "done"
    ticket.intent_status = "active"

    # Record AgentHub attempt for all completions
    project = db.session.get(Project, project_id)
    is_swarm = project and (getattr(project, "git_mode", None) or "swarm") == "swarm"
    attempt = None
    if is_swarm:
        attempt = _create_attempt(
            project_id=project_id,
            ticket_id=ticket.id,
            commit_hash=commit_hash,
            base_hash=base_hash,
            agent_id=agent_id_val,
            summary=summary or None,
            initial_status="proposed",
        )
        _post_event(
            _ticket_channel(str(ticket_id)),
            _event_content(
                "validation_started",
                f"Validation started for attempt #{attempt.attempt_num}",
                {
                    "attempt_id": str(attempt.id),
                    "attempt_num": attempt.attempt_num,
                    "commit_hash": commit_hash,
                    "base_hash": base_hash,
                    "wave_num": attempt.wave_num,
                },
            ),
        )
        # Validate immediately: check commit exists in AgentHub
        _validate_attempt(attempt)
        # Post validation result to ticket channel
        if attempt.status == "accepted":
            _post_event(
                _ticket_channel(str(ticket_id)),
                _event_content(
                    "validation_passed",
                    f"Validation passed for attempt #{attempt.attempt_num}",
                    {
                        "attempt_id": str(attempt.id),
                        "attempt_num": attempt.attempt_num,
                        "commit_hash": commit_hash,
                        "base_hash": base_hash,
                        "wave_num": attempt.wave_num,
                    },
                ),
            )
            _post_event(
                _ticket_channel(str(ticket_id)),
                _event_content(
                    "attempt_published",
                    f"Attempt #{attempt.attempt_num} published"
                    + (f" at {commit_hash[:12]}" if commit_hash else ""),
                    {
                        "attempt_id": str(attempt.id),
                        "attempt_num": attempt.attempt_num,
                        "commit_hash": commit_hash,
                        "base_hash": base_hash,
                        "wave_num": attempt.wave_num,
                        "status": attempt.status,
                    },
                ),
            )
        else:
            _post_event(
                _ticket_channel(str(ticket_id)),
                _event_content(
                    "validation_failed",
                    attempt.validation_error or "Attempt validation failed",
                    {
                        "attempt_id": str(attempt.id),
                        "attempt_num": attempt.attempt_num,
                        "commit_hash": commit_hash,
                        "base_hash": base_hash,
                        "wave_num": attempt.wave_num,
                        "status": attempt.status,
                    },
                ),
            )

    db.session.commit()

    if is_swarm:
        try:
            _dispatch_unblocked_queued(project_id)
        except Exception as exc:
            current_app.logger.warning("Dispatch queued failed: %s", exc)

    return jsonify({"message": "Complete", "ticket_id": str(ticket.id)})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/attempts", methods=["GET"])
def ticket_attempts_list(project_id, ticket_id):
    """List all attempts for a ticket, newest first."""
    Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project_id, ticket_id=ticket_id)
        .order_by(TicketAttempt.attempt_num.desc())
        .all()
    )
    include_output = request.args.get("include_test_output", "false").lower() == "true"
    project = db.session.get(Project, project_id)
    frontier = getattr(project, "shipped_frontier", None) or None
    return jsonify([
        _attempt_to_json(a, include_test_output=include_output, shipped_frontier=frontier)
        for a in attempts
    ])


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/ledger", methods=["GET"])
def ticket_ledger(project_id, ticket_id):
    project = _get_project_or_404(project_id)
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    return jsonify(_project_ticket_ledger(project, ticket))


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/context", methods=["GET"])
def ticket_context(project_id, ticket_id):
    project = _get_project_or_404(project_id)
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    agent = (request.args.get("agent") or "").strip().lower() in {"1", "true", "yes"}
    return jsonify(_build_ticket_context(project, ticket, agent=agent, fetch_posts=_fetch_channel_posts))


@api_bp.route("/projects/<uuid:project_id>/attempts", methods=["GET"])
def project_attempts_list(project_id):
    """List attempts across a project with optional ticket/status filters."""
    project = _get_project_or_404(project_id)
    query = (
        TicketAttempt.query
        .filter_by(project_id=project_id)
        .join(Ticket, Ticket.id == TicketAttempt.ticket_id)
    )
    ticket_id = (request.args.get("ticket_id") or "").strip()
    status = (request.args.get("status") or "").strip()
    if ticket_id:
        try:
            ticket_uuid = str(UUID(ticket_id))
        except ValueError:
            return jsonify({"error": "ticket_id must be a valid UUID"}), 400
        query = query.filter(TicketAttempt.ticket_id == ticket_uuid)
    if status:
        query = query.filter(TicketAttempt.status == status)
    attempts = (
        query
        .order_by(TicketAttempt.created_at.desc(), TicketAttempt.attempt_num.desc())
        .all()
    )
    return jsonify([_attempt_inspection_json(project, attempt) for attempt in attempts])


@api_bp.route("/projects/<uuid:project_id>/attempts/<uuid:attempt_id>", methods=["GET"])
def project_attempt_detail(project_id, attempt_id):
    """Fetch a single attempt with agent-friendly inspection metadata."""
    project = _get_project_or_404(project_id)
    attempt = TicketAttempt.query.filter_by(project_id=project_id, id=attempt_id).first_or_404()
    return jsonify(_attempt_inspection_json(project, attempt))


@api_bp.route("/projects/<uuid:project_id>/attempts/<uuid:attempt_id>/files", methods=["GET"])
def project_attempt_files(project_id, attempt_id):
    """Return structured file stats for an attempt."""
    project = _get_project_or_404(project_id)
    attempt = TicketAttempt.query.filter_by(project_id=project_id, id=attempt_id).first_or_404()
    report = _inspect_changed_files(project, attempt)
    return jsonify(report)


@api_bp.route("/projects/<uuid:project_id>/attempts/<uuid:attempt_id>/diff", methods=["GET"])
def project_attempt_diff(project_id, attempt_id):
    """Return a diff for an attempt, optionally filtered to one file."""
    project = _get_project_or_404(project_id)
    attempt = TicketAttempt.query.filter_by(project_id=project_id, id=attempt_id).first_or_404()
    file_path = (request.args.get("file") or "").strip() or None
    max_bytes_raw = (request.args.get("max_bytes") or "").strip()
    max_bytes = None
    if max_bytes_raw:
        try:
            max_bytes = int(max_bytes_raw)
        except ValueError:
            return jsonify({"error": "max_bytes must be an integer"}), 400
        if max_bytes < 0:
            return jsonify({"error": "max_bytes must be >= 0"}), 400
    return jsonify(_inspect_diff(project, attempt, file_path=file_path, max_bytes=max_bytes))


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/attempts/<uuid:attempt_id>/accept", methods=["POST"])
def ticket_attempt_accept(project_id, ticket_id, attempt_id):
    """Accept a ticket attempt. Supersedes any previously accepted attempt for the same ticket."""
    attempt = TicketAttempt.query.filter_by(
        project_id=project_id, ticket_id=ticket_id, id=attempt_id
    ).first_or_404()
    try:
        # Supersede any existing accepted attempt for this ticket
        prev_accepted = _get_accepted_attempt(ticket_id)
        if prev_accepted and str(prev_accepted.id) != str(attempt_id):
            try:
                _transition_attempt(prev_accepted, "superseded", reason="superseded by newer acceptance")
            except ValueError:
                pass  # already in a terminal state
        _transition_attempt(attempt, "accepted")
        db.session.commit()
        _post_event(
            _ticket_channel(str(ticket_id)),
            _event_content(
                "attempt_accepted",
                f"Attempt #{attempt.attempt_num} accepted"
                + (f" at {attempt.agenthub_commit_hash[:12]}" if attempt.agenthub_commit_hash else ""),
                {
                    "attempt_id": str(attempt.id),
                    "attempt_num": attempt.attempt_num,
                    "commit_hash": attempt.agenthub_commit_hash,
                    "wave_num": attempt.wave_num,
                },
            ),
        )
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 409
    return jsonify(_attempt_to_json(attempt))


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/attempts/<uuid:attempt_id>/reject", methods=["POST"])
def ticket_attempt_reject(project_id, ticket_id, attempt_id):
    """Reject a ticket attempt. Optionally post feedback to AgentHub channel."""
    attempt = TicketAttempt.query.filter_by(
        project_id=project_id, ticket_id=ticket_id, id=attempt_id
    ).first_or_404()
    data = request.json or {}
    reason = (data.get("reason") or "").strip()
    try:
        _transition_attempt(attempt, "rejected", reason=reason or "rejected via API")
        db.session.commit()
        _post_event(
            _ticket_channel(str(ticket_id)),
            _event_content(
                "attempt_rejected",
                f"Attempt #{attempt.attempt_num} rejected" + (f": {reason}" if reason else ""),
                {
                    "attempt_id": str(attempt.id),
                    "attempt_num": attempt.attempt_num,
                    "reason": reason,
                    "wave_num": attempt.wave_num,
                },
            ),
        )
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 409
    return jsonify(_attempt_to_json(attempt))


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/cancel-requested", methods=["GET"])
def ticket_cancel_requested(project_id, ticket_id):
    """Phase 1: Poll by agent to see if cancellation was requested. Auth: Bearer."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    job = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.status.in_(["pending", "running"]),
    ).order_by(AgentJob.created_at.desc()).first()
    requested = bool(job and job.cancel_requested)
    return jsonify({"cancel_requested": requested})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/cancel", methods=["POST"])
def cancel_ticket_execution_api(project_id, ticket_id):
    """Request cancellation. Sets cancel_requested on the running/pending job; agent polls and exits."""
    Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    job = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.status.in_(["pending", "running"]),
    ).order_by(AgentJob.created_at.desc()).first()
    if job:
        job.cancel_requested = True
        db.session.commit()
    return jsonify({"message": "Cancellation requested"}), 200


# ---------- Phase 1: Queue (worker-facing). Auth: Bearer (TERARCHITECT_WORKER_API_KEY). ----------

@api_bp.route("/worker/projects", methods=["GET"])
def worker_projects():
    """Return all project IDs (and names) for coordinator discovery. Requires worker API auth."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    projects = Project.query.order_by(Project.created_at.asc()).all()
    return jsonify({
        "projects": [{"id": str(p.id), "name": p.name or "Untitled"} for p in projects],
    }), 200


# ---------------------------------------------------------------------------
# Wave computation helpers (swarm mode)
# ---------------------------------------------------------------------------

@api_bp.route("/worker/jobs/start", methods=["POST"])
def worker_jobs_start():
    """Claim one pending job. Body: optional {"project_id": "<uuid>"}.
    For swarm-mode projects, skips tickets whose graph nodes/edges conflict with
    any currently running ticket, serialising overlapping work automatically.
    Returns 200 + job or 204 (nothing available right now)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    project_id = data.get("project_id")

    if project_id:
        try:
            project_id = UUID(project_id) if isinstance(project_id, str) else project_id
        except (ValueError, TypeError):
            return jsonify({"error": "project_id must be a valid UUID"}), 400
        project = db.session.get(Project, project_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404

        if getattr(project, "git_mode", None) == "swarm":
            job = _claim_swarm_job(project_id)
        else:
            job = (
                AgentJob.query.filter_by(project_id=project_id, status="pending")
                .order_by(AgentJob.created_at.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
    else:
        job = (
            AgentJob.query.filter_by(status="pending")
            .order_by(AgentJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )

    if not job:
        return "", 204
    job.status = "running"
    db.session.commit()
    return jsonify(_job_to_response(job)), 200


@api_bp.route("/worker/jobs/<uuid:job_id>/complete", methods=["POST"])
def worker_jobs_complete(job_id):
    """Phase 1: Mark job completed when container exits."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    job = AgentJob.query.filter_by(id=job_id).first_or_404()
    if job.status != "running":
        return jsonify({"error": "Job not running", "status": job.status}), 409
    job.status = "completed"
    db.session.commit()
    return jsonify({"message": "Job completed", "job_id": str(job_id)})


@api_bp.route("/worker/jobs/<uuid:job_id>/fail", methods=["POST"])
def worker_jobs_fail(job_id):
    """Phase 1: Mark job failed when container exits. Moves ticket back to backlog and increments failed_count."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    job = AgentJob.query.filter_by(id=job_id).first_or_404()
    if job.status != "running":
        return jsonify({"error": "Job not running", "status": job.status}), 409
    job.status = "failed"
    if job.ticket_id:
        ticket = Ticket.query.filter_by(id=job.ticket_id).first()
        if ticket:
            ticket.column_id = "queued"
            ticket.failed_count = (ticket.failed_count or 0) + 1
    db.session.commit()
    if job.ticket_id:
        _post_event(
            _ticket_channel(str(job.ticket_id)),
            _event_content(
                "retry_requested",
                f"Job {str(job_id)[:8]} failed; ticket re-queued for retry",
                {"job_id": str(job_id), "ticket_id": str(job.ticket_id)},
            ),
        )
    return jsonify({"message": "Job failed", "job_id": str(job_id)})


@api_bp.route("/worker/jobs/reset-stale", methods=["POST"])
def worker_jobs_reset_stale():
    """Phase 1: Reset jobs stuck in 'running' state (e.g. after coordinator restart). Auth: Bearer.
    Body: optional {"max_age_seconds": N} — only reset running jobs older than N seconds (default 3600)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    try:
        max_age = int(data.get("max_age_seconds", 3600))
    except (TypeError, ValueError):
        max_age = 3600
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    stale = AgentJob.query.filter(
        AgentJob.status == "running",
        AgentJob.updated_at < cutoff,
    ).all()
    count = len(stale)
    for job in stale:
        job.status = "failed"
        if job.ticket_id:
            ticket = Ticket.query.filter_by(id=job.ticket_id).first()
            if ticket:
                ticket.column_id = "queued"
                ticket.failed_count = (ticket.failed_count or 0) + 1
    db.session.commit()
    current_app.logger.info("Reset %d stale running jobs (older than %ds)", count, max_age)
    return jsonify({"reset": count, "max_age_seconds": max_age})


@api_bp.route("/readiness", methods=["GET"])
def migration_readiness():
    """Checkpoint verification for the AgentHub-native conversion (plan 13.2).
    Returns pass/fail for each migration checkpoint A-G."""
    from sqlalchemy import inspect as _sa_inspect

    inspector = _sa_inspect(db.engine)
    tables = set(inspector.get_table_names())

    def _has_column(table, col):
        if table not in tables:
            return False
        return any(c["name"] == col for c in inspector.get_columns(table))

    checkpoints = {
        "A_data_model": {
            "description": "Core tables and intent fields exist",
            "checks": {
                "ticket_attempts": "ticket_attempts" in tables,
                "ship_runs": "ship_runs" in tables,
                "composite_workspaces": "composite_workspaces" in tables,
                "evidence_bundles": "evidence_bundles" in tables,
                "evidence_checks": "evidence_checks" in tables,
                "tickets.shipped_frontier_on_projects": _has_column("projects", "shipped_frontier"),
                "projects.verification_policy": _has_column("projects", "verification_policy"),
                "tickets.intent_status": _has_column("tickets", "intent_status"),
                "tickets.acceptance_criteria": _has_column("tickets", "acceptance_criteria"),
                "prs_table_dropped": "prs" not in tables,
                "pr_review_comments_dropped": "pr_review_comments" not in tables,
            },
        },
        "B_write_path": {
            "description": "Swarm ticket completion creates attempts, not PRs",
            "checks": {
                "ticket_attempts_exists": "ticket_attempts" in tables,
                "prs_dropped": "prs" not in tables,
                "agent_jobs_no_review_kind": not _has_column("agent_jobs", "pr_number"),
            },
        },
        "C_read_path": {
            "description": "Ship Room, display_state, and CLI ship commands available",
            "checks": {
                "ship_runs_exists": "ship_runs" in tables,
                "display_state_computable": _has_column("tickets", "intent_status"),
                "frontier_readable": _has_column("projects", "shipped_frontier"),
            },
        },
        "D_composition": {
            "description": "Shipper can compose release branches",
            "checks": {
                "ship_runs_has_release_branch": _has_column("ship_runs", "release_branch"),
                "ship_runs_has_test_status": _has_column("ship_runs", "test_status"),
                "ship_runs_has_composed_commit": _has_column("ship_runs", "composed_commit_hash"),
            },
        },
        "E_ship": {
            "description": "Ship advances frontier and marks attempts shipped",
            "checks": {
                "projects_has_frontier": _has_column("projects", "shipped_frontier"),
                "ticket_attempts_shipped_status": "ticket_attempts" in tables,
                "ship_runs_has_shipped_at": _has_column("ship_runs", "shipped_at"),
            },
        },
        "F_composite_workspace": {
            "description": "Composite Workspace: compose, bless, promote",
            "checks": {
                "composite_workspaces_exists": "composite_workspaces" in tables,
                "composite_workspaces_has_status": _has_column("composite_workspaces", "status"),
                "projects_has_blessed_workspace": _has_column("projects", "blessed_workspace_id"),
                "feature_flag_exists": True,  # ENABLE_COMPOSITE_WORKSPACE env var documented
            },
        },
        "G_cleanup": {
            "description": "PR-per-ticket path fully removed",
            "checks": {
                "prs_table_dropped": "prs" not in tables,
                "pr_review_comments_dropped": "pr_review_comments" not in tables,
                "merge_runs_renamed_to_ship_runs": "merge_runs" not in tables and "ship_runs" in tables,
            },
        },
        "H_verification_engine": {
            "description": "Evidence bundles and policy configuration are available",
            "checks": {
                "evidence_bundles_exists": "evidence_bundles" in tables,
                "evidence_checks_exists": "evidence_checks" in tables,
                "projects_has_verification_policy": _has_column("projects", "verification_policy"),
            },
        },
    }

    result = {}
    all_pass = True
    for key, cp in checkpoints.items():
        checks = cp["checks"]
        passed = all(checks.values())
        all_pass = all_pass and passed
        result[key] = {
            "description": cp["description"],
            "passed": passed,
            "checks": checks,
        }

    return jsonify({
        "all_checkpoints_passed": all_pass,
        "checkpoints": result,
    })


@api_bp.route("/ready", methods=["GET"])
def execution_ready():
    """Readiness check: env vars for running agents + active feature flags.
    Returns { ready, missing, features }."""
    ready, missing = check_execution_readiness()
    return jsonify({
        "ready": ready,
        "missing": [{"key": k, "label": l} for k, l in missing],
        "features": {
            # Feature flags — set to 1/true to enable, 0/false to disable.
            # Remove each flag when the corresponding phase is stable.
            "composite_workspace": _workspace_enabled(),
        },
    })


@api_bp.route("/projects/<uuid:project_id>/notes", methods=["GET", "POST"])
def notes(project_id):
    """List notes or create a new one."""
    if request.method == "GET":
        notes = Note.query.filter_by(project_id=project_id).all()
        return jsonify([_note_to_json(n) for n in notes])

    if request.method == "POST":
        data = request.json or {}
        note = Note(
            project_id=project_id,
            node_id=_join_note_link_ids(data.get("node_ids")),
            edge_id=_join_note_link_ids(data.get("edge_ids")),
            title=data.get("title"),
            content=data.get("content"),
        )
        db.session.add(note)
        db.session.commit()
        content = ((data.get("title") or "") + " " + (data.get("content") or "")).strip()
        if content:
            upsert_embedding(project_id, "note", note.id, content)
        return jsonify(_note_to_json(note)), 201


@api_bp.route("/projects/<uuid:project_id>/notes/<uuid:note_id>", methods=["GET", "PATCH", "DELETE"])
def note_detail(project_id, note_id):
    """Get, update, or delete a single note."""
    note = Note.query.filter_by(project_id=project_id, id=note_id).first_or_404()

    if request.method == "GET":
        return jsonify(_note_to_json(note))

    if request.method == "PATCH":
        data = request.json or {}
        if "title" in data:
            note.title = data["title"]
        if "content" in data:
            note.content = data["content"]
        if "node_ids" in data:
            note.node_id = _join_note_link_ids(data.get("node_ids"))
        if "edge_ids" in data:
            note.edge_id = _join_note_link_ids(data.get("edge_ids"))
        db.session.commit()
        content = ((note.title or "") + " " + (note.content or "")).strip()
        if content:
            upsert_embedding(project_id, "note", note.id, content)
        return jsonify(_note_to_json(note))

    if request.method == "DELETE":
        delete_embeddings_for_source(project_id, "note", note.id)
        db.session.delete(note)
        db.session.commit()
        return jsonify({"message": "Note deleted"})


@api_bp.route("/rag/search", methods=["POST"])
def rag_search():
    """Search embeddings using vector similarity (embedding service + pgvector)."""
    data = request.json or {}
    project_id = data.get("project_id")
    query = data.get("query")
    try:
        limit = min(int(data.get("limit", 5)), 50)
    except (TypeError, ValueError):
        limit = 5
    source_types = data.get("source_types", ["node", "edge", "note", "ticket", "ticket_comment"])

    if not query:
        return jsonify({"error": "Query is required"}), 400
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    try:
        project_uuid = UUID(project_id)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid project_id"}), 400
    if db.session.get(Project, project_uuid) is None:
        return jsonify({"error": "Project not found"}), 404

    try:
        query_embedding = embed_single(query)
    except Exception as e:
        current_app.logger.warning("Embedding service error: %s", e)
        return jsonify({"error": "Embedding service unavailable", "detail": str(e)}), 503

    vec_str = "[" + ",".join(str(f) for f in query_embedding) + "]"
    rows = db.session.execute(
        text("""
            SELECT id, project_id, source_type, source_id, content,
                   (embedding <-> CAST(:vec AS vector)) AS distance
            FROM rag_embeddings
            WHERE project_id = :project_id AND source_type = ANY(:source_types)
            ORDER BY embedding <-> CAST(:vec AS vector)
            LIMIT :limit
        """),
        {"vec": vec_str, "project_id": project_uuid, "source_types": source_types, "limit": limit},
    ).fetchall()

    return jsonify({
        "results": [
            {
                "id": str(r.id),
                "project_id": str(r.project_id),
                "source_type": r.source_type,
                "source_id": str(r.source_id),
                "content": r.content,
                "distance": float(r.distance),
            }
            for r in rows
        ],
    })


@api_bp.route("/projects/<uuid:project_id>/memory/index", methods=["POST"])
def memory_index(project_id):
    """Index documents into project memory (HippoRAG). Locked per project. Auth: Bearer (worker)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    if db.session.get(Project, project_id) is None:
        return jsonify({"error": "Project not found"}), 404
    data = request.json or {}
    docs = data.get("docs")
    if not docs or not isinstance(docs, list):
        return jsonify({"error": "docs (list of strings) is required"}), 400
    base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
    if not base_save_dir:
        return jsonify({"error": "MEMORY_SAVE_DIR not configured"}), 503
    try:
        from utils.memory import index as memory_index_fn, get_hipporag_kwargs
        memory_index_fn(project_id, docs, base_save_dir, **get_hipporag_kwargs())
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.exception("Memory index failed")
        return jsonify({"error": "Index failed", "detail": str(e)}), 500
    return jsonify({"message": "Indexed", "count": len(docs)})


@api_bp.route("/projects/<uuid:project_id>/memory/retrieve", methods=["POST"])
def memory_retrieve(project_id):
    """Retrieve relevant passages for queries (HippoRAG). Locked per project. Auth: Bearer (worker)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    if db.session.get(Project, project_id) is None:
        return jsonify({"error": "Project not found"}), 404
    data = request.json or {}
    queries = data.get("queries")
    if not queries or not isinstance(queries, list):
        return jsonify({"error": "queries (list of strings) is required"}), 400
    num_to_retrieve = data.get("num_to_retrieve")
    base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
    if not base_save_dir:
        return jsonify({"error": "MEMORY_SAVE_DIR not configured"}), 503
    try:
        from utils.memory import retrieve as memory_retrieve_fn, get_hipporag_kwargs
        results = memory_retrieve_fn(
            project_id, queries, base_save_dir,
            num_to_retrieve=num_to_retrieve,
            **get_hipporag_kwargs(),
        )
        # If no memory yet (e.g. existing project), bootstrap one doc then retry so agent gets something
        if results and all(len((r.get("docs") or [])) == 0 for r in results):
            project = db.session.get(Project, project_id)
            if project:
                _bootstrap_project_memory(project)
                results = memory_retrieve_fn(
                    project_id, queries, base_save_dir,
                    num_to_retrieve=num_to_retrieve,
                    **get_hipporag_kwargs(),
                )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.exception("Memory retrieve failed")
        return jsonify({"error": "Retrieve failed", "detail": str(e)}), 500
    return jsonify({"results": results})


@api_bp.route("/projects/<uuid:project_id>/memory/delete", methods=["POST"])
def memory_delete(project_id):
    """Remove documents from project memory (HippoRAG). Locked per project. Auth: Bearer (worker)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    if db.session.get(Project, project_id) is None:
        return jsonify({"error": "Project not found"}), 404
    data = request.json or {}
    docs = data.get("docs")
    if not docs or not isinstance(docs, list):
        return jsonify({"error": "docs (list of strings) is required"}), 400
    base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
    if not base_save_dir:
        return jsonify({"error": "MEMORY_SAVE_DIR not configured"}), 503
    try:
        from utils.memory import delete as memory_delete_fn, get_hipporag_kwargs
        memory_delete_fn(project_id, docs, base_save_dir, **get_hipporag_kwargs())
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.exception("Memory delete failed")
        return jsonify({"error": "Delete failed", "detail": str(e)}), 500
    return jsonify({"message": "Deleted", "count": len(docs)})


@api_bp.route("/projects/<uuid:project_id>/start", methods=["POST"])
def project_start(project_id):
    """Move all backlog tickets to queued, then immediately dispatch those with no unfinished deps.
    This is the 'Go' button — VP approves the full backlog to run autonomously."""
    _get_project_or_404(project_id)
    backlog_tickets = Ticket.query.filter_by(project_id=project_id, column_id="backlog").all()
    for t in backlog_tickets:
        t.column_id = "queued"
    db.session.commit()
    _dispatch_unblocked_queued(project_id)
    queued_count = Ticket.query.filter_by(project_id=project_id, column_id="queued").count()
    in_progress_count = Ticket.query.filter_by(project_id=project_id, column_id="in_progress").count()
    return jsonify({
        "queued": queued_count,
        "dispatched": in_progress_count,
        "message": f"Moved {len(backlog_tickets)} tickets to queued; {in_progress_count} dispatched immediately.",
    })




# ---------------------------------------------------------------------------
# Ship Room legacy compatibility routes.
# Phase 1 freezes the target vocabulary as shipped_frontier -> accepted
# TicketAttempt -> promotion candidate -> ShipRun. The concrete routes below
# still expose wave-oriented endpoints until later phases replace them.
# ---------------------------------------------------------------------------

def _find_matching_candidate(project_id, selected_attempt_ids: list[str], base_root_hash: str | None):
    normalized_ids = [str(attempt_id) for attempt_id in selected_attempt_ids or []]
    candidates = (
        PromotionCandidate.query
        .filter_by(project_id=project_id)
        .order_by(PromotionCandidate.created_at.desc())
        .all()
    )
    for candidate in candidates:
        if list(candidate.selected_attempt_ids or []) != normalized_ids:
            continue
        if (candidate.base_root_hash or None) != (base_root_hash or None):
            continue
        if candidate.status == "superseded":
            continue
        return candidate
    return None


def _ensure_candidate_from_attempt_ids(project, selected_attempt_ids: list[str]):
    snapshot = _build_promotion_candidate_snapshot(project, selected_attempt_ids)
    candidate = _find_matching_candidate(project.id, snapshot["selected_attempt_ids"], snapshot["base_root_hash"])
    if candidate is None:
        candidate = PromotionCandidate(
            project_id=str(project.id),
            selected_attempt_ids=snapshot["selected_attempt_ids"],
            selected_leaf_hashes=snapshot["selected_leaf_hashes"],
            base_root_hash=snapshot["base_root_hash"],
            status=snapshot["status"],
            validation_summary=snapshot["validation_summary"],
            conflict_summary=snapshot["conflict_summary"],
        )
        db.session.add(candidate)
        db.session.flush()
    return candidate, snapshot


def _ensure_wave_candidate(project, wave_num: int):
    tickets = Ticket.query.filter_by(project_id=project.id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [ticket for ticket in tickets if waves.get(str(ticket.id), 0) == wave_num]
    if not wave_tickets:
        return None, None, None, f"No tickets in wave {wave_num}"

    accepted_attempts = [_get_accepted_attempt(ticket.id) for ticket in wave_tickets]
    if not any(accepted_attempts):
        return None, None, None, "No accepted attempts found for this wave. Agents must complete tickets first."
    if not all(accepted_attempts):
        missing = [wave_tickets[i].title for i, attempt in enumerate(accepted_attempts) if not attempt]
        return None, None, None, f"Some tickets have no accepted attempt yet: {', '.join(missing[:3])}"

    candidate, snapshot = _ensure_candidate_from_attempt_ids(
        project,
        [str(attempt.id) for attempt in accepted_attempts if attempt is not None],
    )
    return candidate, snapshot, wave_tickets, None


def _candidate_membership_payload(candidate: PromotionCandidate) -> dict:
    attempts = _candidate_attempts(candidate)
    tickets: list[dict] = []
    for attempt in attempts:
        ticket = db.session.get(Ticket, attempt.ticket_id)
        if ticket is None:
            continue
        tickets.append({
            "id": str(ticket.id),
            "title": ticket.title,
            "column_id": ticket.column_id,
            "depends_on_ticket_ids": [str(dep_id) for dep_id in (ticket.depends_on_ticket_ids or [])],
        })
    return {
        "attempts": [_attempt_to_json(attempt) for attempt in attempts],
        "tickets": tickets,
        "commit_hashes": _candidate_commit_hashes(candidate),
        "legacy_wave_num": _candidate_legacy_wave_num(candidate),
    }


def _ship_run_context(run: ShipRun) -> dict:
    candidate = db.session.get(PromotionCandidate, run.promotion_candidate_id) if run.promotion_candidate_id else None
    project = db.session.get(Project, run.project_id)
    if candidate is not None:
        membership = _candidate_membership_payload(candidate)
        return {
            "project": project,
            "candidate": candidate,
            "membership": membership,
            "wave_tickets": membership["tickets"],
            "commit_hashes": membership["commit_hashes"],
            "wave_num": membership["legacy_wave_num"],
            "validation_errors": _validate_promotion_candidate(candidate, project),
        }

    tickets = Ticket.query.filter_by(project_id=run.project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [ticket for ticket in tickets if waves.get(str(ticket.id), 0) == run.wave_num]
    return {
        "project": project,
        "candidate": None,
        "membership": None,
        "wave_tickets": [
            {"id": str(ticket.id), "title": ticket.title, "column_id": ticket.column_id}
            for ticket in wave_tickets
        ],
        "commit_hashes": _collect_wave_commit_hashes(wave_tickets, project),
        "wave_num": run.wave_num,
        "validation_errors": _validate_wave_composition(project, run.wave_num, tickets, waves, wave_tickets),
    }


def _ship_run_detail_payload(run: ShipRun) -> dict:
    payload = _ship_run_to_json(run)
    context = _ship_run_context(run)
    candidate = context["candidate"]
    payload["candidate"] = _promotion_candidate_to_json(candidate, include_attempts=True) if candidate else None
    payload["membership"] = context["membership"]
    payload["validation_errors"] = context["validation_errors"]
    payload["wave_tickets"] = context["wave_tickets"]
    payload["commit_hashes"] = context["commit_hashes"]
    payload["evidence_summary"] = _ship_run_evidence_summary(run)
    return payload

def _wave_next_actions(*, wave_num: int, blockers: list[str], all_done: bool, ship_run, can_compose: bool, can_ship: bool) -> list[str]:
    actions: list[str] = []
    if blockers:
        if any("unknown ticket" in b.lower() or "unknown dependency" in b.lower() for b in blockers):
            actions.append("Fix or remove dependency references that point to missing tickets.")
        if any("cycle" in b.lower() for b in blockers):
            actions.append("Break the dependency cycle so tickets can be ordered into earlier waves.")
        if any("no accepted attempt" in b.lower() for b in blockers):
            actions.append("Wait for every ticket in this candidate set to reach an accepted attempt.")
        if any("not shipped" in b.lower() for b in blockers):
            actions.append("Ship prerequisite promotion work first, then re-run candidate review or compose.")
        if any("not the current frontier" in b.lower() or "base " in b.lower() for b in blockers):
            actions.append("Refresh stale tickets from the current frontier before composing or shipping.")
    if not all_done:
        actions.append("Finish the remaining tickets in this candidate set before composing.")
    if can_compose:
        actions.append("Compose this promotion candidate when you want a release-branch preview.")
    elif ship_run and ship_run.status in ("queued", "composing", "running"):
        actions.append("Wait for the active ship run to finish composing.")
    elif ship_run and ship_run.status == "ready_to_ship":
        actions.append("Review the composed ship run and diff before shipping.")
    elif ship_run and ship_run.status == "shipped":
        actions.append("Candidate already shipped; inspect the shipped frontier or later candidates.")
    if can_ship:
        actions.append("Ship the ready ShipRun to advance the shipped frontier.")

    deduped: list[str] = []
    for action in actions:
        if action not in deduped:
            deduped.append(action)
    return deduped


def _wave_detail(project_id, wave_num: int) -> dict:
    """Build the full legacy wave detail payload used by current ship endpoints."""
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    analysis = _analyze_wave_dependencies(tickets)
    waves = analysis["waves"]
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == wave_num]

    accepted_attempts = []
    stale_details = []
    for t in wave_tickets:
        a = _get_accepted_attempt(t.id)
        if a:
            accepted_attempts.append(a)

    project = db.session.get(Project, project_id)
    frontier = getattr(project, "shipped_frontier", None) or None

    # Most recent non-failed ship run for this wave
    ship_run = (
        ShipRun.query
        .filter_by(project_id=project_id, wave_num=wave_num)
        .filter(ShipRun.status.notin_(["failed"]))
        .order_by(ShipRun.created_at.desc())
        .first()
    )

    accepted_ticket_ids = {str(a.ticket_id) for a in accepted_attempts}
    all_done = bool(wave_tickets) and all(str(t.id) in accepted_ticket_ids for t in wave_tickets)

    for a in accepted_attempts:
        if frontier and a.base_hash and a.base_hash != frontier:
            stale_details.append({
                "ticket_id": str(a.ticket_id),
                "attempt_id": str(a.id),
                "attempt_base_hash": a.base_hash,
                "shipped_frontier": frontier,
                "reason": f"Attempt base {a.base_hash[:12]} differs from frontier {frontier[:12]}.",
            })

    compose_validation_errors = []
    if wave_tickets:
        if ship_run and ship_run.promotion_candidate_id:
            candidate = db.session.get(PromotionCandidate, ship_run.promotion_candidate_id)
            compose_validation_errors = _validate_promotion_candidate(candidate, project) if candidate else []
        else:
            compose_validation_errors = _validate_wave_composition(
                project, wave_num, tickets, waves, wave_tickets, analysis=analysis
            )

    ship_validation_errors = list(compose_validation_errors)
    if ship_run and ship_run.status == "ready_to_ship" and frontier and ship_run.base_main_hash and ship_run.base_main_hash != frontier:
        ship_validation_errors.append(
            f"Ship run base {ship_run.base_main_hash[:12]} is not the current frontier {frontier[:12]}."
        )

    can_compose = (
        all_done and
        len(accepted_attempts) > 0 and
        (ship_run is None or ship_run.status in ("compose_failed", "failed")) and
        not compose_validation_errors
    )
    can_ship = bool(ship_run and ship_run.status == "ready_to_ship" and not ship_validation_errors)

    stale_count = len(stale_details)

    ticket_payloads = []
    blockers: list[str] = list(compose_validation_errors)
    for ticket in wave_tickets:
        payload = _ticket_to_json(ticket)
        explanation = analysis["ticket_explanations"].get(str(ticket.id), {})
        payload.update({
            "wave_num": explanation.get("wave_num", wave_num),
            "dependency_reason": explanation.get("dependency_reason"),
            "blockers": explanation.get("blockers", []),
            "unknown_dependency_ids": explanation.get("unknown_dependency_ids", []),
            "dependency_cycles": explanation.get("dependency_cycles", []),
        })
        latest = payload.get("latest_attempt") or {}
        if latest.get("stale"):
            payload.setdefault("blockers", []).append("Latest attempt is stale against the shipped frontier.")
        accepted = payload.get("accepted_attempt") or {}
        if accepted.get("stale"):
            payload.setdefault("blockers", []).append("Accepted attempt is stale against the shipped frontier.")
        blockers.extend(payload.get("blockers", []))
        ticket_payloads.append(payload)

    for stale in stale_details:
        blockers.append(stale["reason"])

    deduped_blockers: list[str] = []
    for blocker in blockers:
        if blocker and blocker not in deduped_blockers:
            deduped_blockers.append(blocker)

    next_actions = _wave_next_actions(
        wave_num=wave_num,
        blockers=deduped_blockers,
        all_done=all_done,
        ship_run=ship_run,
        can_compose=can_compose,
        can_ship=can_ship,
    )

    return {
        "wave_num": wave_num,
        "tickets": ticket_payloads,
        "accepted_attempts": [
            _attempt_to_json(a, shipped_frontier=frontier) for a in accepted_attempts
        ],
        "ship_run": _ship_run_to_json(ship_run) if ship_run else None,
        "can_compose": can_compose,
        "can_ship": can_ship,
        "all_done": all_done,
        "shipped_frontier": frontier,
        "stale_count": stale_count,
        "stale_details": stale_details,
        "validation": {
            "compose": compose_validation_errors,
            "ship": ship_validation_errors,
        },
        "dependency_cycles": analysis["dependency_cycles"],
        "unknown_dependency_refs": analysis["unknown_dependency_refs"],
        "blockers": deduped_blockers,
        "next_actions": next_actions,
    }


@api_bp.route("/projects/<uuid:project_id>/ship/waves", methods=["GET"])
def ship_waves(project_id):
    """List legacy wave groupings with accepted attempt counts and ship run status."""
    _get_project_or_404(project_id)
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    if not tickets:
        return jsonify([])
    explain = (request.args.get("explain") or "").strip().lower() in ("1", "true", "yes")
    analysis = _analyze_wave_dependencies(tickets)
    waves = analysis["waves"]
    runs = {
        r.wave_num: r
        for r in ShipRun.query.filter_by(project_id=project_id).order_by(ShipRun.created_at.desc()).all()
    }

    wave_map: dict = {}
    for t in tickets:
        w = waves.get(str(t.id), 0)
        wave_map.setdefault(w, {"wave_num": w, "tickets": [], "accepted_count": 0})
        wave_map[w]["tickets"].append(str(t.id))
        if _get_accepted_attempt(t.id):
            wave_map[w]["accepted_count"] += 1

    for w, entry in wave_map.items():
        entry["ticket_count"] = len(entry.pop("tickets"))
        wave_tix = [t for t in tickets if waves.get(str(t.id), 0) == w]
        entry["all_done"] = bool(wave_tix) and all(
            _get_accepted_attempt(t.id) for t in wave_tix
        )
        run = runs.get(w)
        entry["ship_run"] = _ship_run_to_json(run) if run else None
        if explain:
            detail = _wave_detail(project_id, w)
            entry["can_compose"] = detail["can_compose"]
            entry["can_ship"] = detail["can_ship"]
            entry["blockers"] = detail["blockers"]
            entry["next_actions"] = detail["next_actions"]
            entry["unknown_dependency_refs"] = detail["unknown_dependency_refs"]
            entry["dependency_cycles"] = detail["dependency_cycles"]

    return jsonify(sorted(wave_map.values(), key=lambda x: x["wave_num"]))


@api_bp.route("/projects/<uuid:project_id>/ship/doctor", methods=["GET"])
def ship_doctor(project_id):
    project = _get_project_or_404(project_id)
    return jsonify(_ship_doctor_report(project))


@api_bp.route("/projects/<uuid:project_id>/ship/happy-path", methods=["POST"])
def ship_happy_path(project_id):
    project = _lock_project_for_update(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    data = request.json or {}
    ticket_id = (data.get("ticket_id") or "").strip()
    if not ticket_id:
        return jsonify({"error": "ticket_id is required"}), 400

    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first()
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    attempt = _get_accepted_attempt(ticket.id)
    if attempt is None:
        return jsonify({
            "error": "Ticket has no accepted attempt yet.",
            "hint": "Accept an attempt before using ship happy-path.",
            "next_commands": [f"ta ticket attempts {project_id} {ticket_id}"],
        }), 409

    candidate, snapshot = _ensure_candidate_from_attempt_ids(project, [str(attempt.id)])
    candidate.selected_leaf_hashes = snapshot["selected_leaf_hashes"]
    candidate.base_root_hash = snapshot["base_root_hash"]
    candidate.status = snapshot["status"]
    candidate.validation_summary = snapshot["validation_summary"]
    candidate.conflict_summary = snapshot["conflict_summary"]
    db.session.flush()

    if snapshot["status"] == "blocked":
        db.session.commit()
        return jsonify({
            "error": "Candidate composition validation failed.",
            "details": snapshot["validation_summary"].get("blockers", []),
            "hint": "Resolve candidate blockers, then retry ship happy-path.",
            "next_commands": [
                f"ta ship candidate {project_id} {candidate.id}",
                f"ta ship doctor {project_id}",
            ],
        }), 409

    run = (
        ShipRun.query
        .filter_by(project_id=project_id, promotion_candidate_id=candidate.id)
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    if run is None or run.status in ("failed", "compose_failed"):
        run = ShipRun(
            project_id=str(project_id),
            promotion_candidate_id=str(candidate.id),
            wave_num=_candidate_legacy_wave_num(candidate),
            status="queued",
        )
        db.session.add(run)
        db.session.commit()
        return jsonify({
            "status": "queued",
            "attempt_id": str(attempt.id),
            "candidate_id": str(candidate.id),
            "ship_run_id": str(run.id),
            "next_commands": [
                f"ta ship run {project_id} {run.id}",
                f"ta ship candidate {project_id} {candidate.id}",
            ],
        }), 202

    if run.status == "ready_to_ship":
        ship_response = _ship_run_ship_response(
            project,
            run,
            merge_method=str((data.get("merge_method") or "merge")).strip().lower(),
        )
        if getattr(ship_response, "status_code", 200) != 200:
            return ship_response
        shipped = ship_response.get_json()
        return jsonify({
            "status": "shipped",
            "attempt_id": str(attempt.id),
            "candidate_id": str(candidate.id),
            "ship_run_id": str(run.id),
            "shipped_commit_hash": shipped.get("shipped_commit_hash"),
            "evidence_summary": shipped.get("evidence_summary"),
            "next_commands": [
                f"ta ship run {project_id} {run.id}",
                f"ta ship candidate {project_id} {candidate.id}",
            ],
        })

    db.session.commit()
    return jsonify({
        "status": run.status,
        "attempt_id": str(attempt.id),
        "candidate_id": str(candidate.id),
        "ship_run_id": str(run.id),
        "next_commands": [
            f"ta ship run {project_id} {run.id}",
            f"ta ship candidate {project_id} {candidate.id}",
        ],
    })


@api_bp.route("/projects/<uuid:project_id>/ship/candidates", methods=["GET", "POST"])
def ship_candidates(project_id):
    """List or create first-class promotion candidates without altering legacy wave APIs."""
    project = _get_project_or_404(project_id)

    if request.method == "GET":
        status = (request.args.get("status") or "").strip()
        query = PromotionCandidate.query.filter_by(project_id=project_id)
        if status:
            query = query.filter_by(status=status)
        candidates = query.order_by(PromotionCandidate.created_at.desc()).all()
        return jsonify([_promotion_candidate_to_json(candidate) for candidate in candidates])

    data = request.json or {}
    selected_attempt_ids = data.get("selected_attempt_ids")
    if not isinstance(selected_attempt_ids, list):
        return jsonify({"error": "selected_attempt_ids must be a list of accepted attempt ids."}), 400

    candidate, snapshot = _ensure_candidate_from_attempt_ids(project, selected_attempt_ids)
    candidate.selected_leaf_hashes = snapshot["selected_leaf_hashes"]
    candidate.base_root_hash = snapshot["base_root_hash"]
    candidate.status = snapshot["status"]
    candidate.validation_summary = snapshot["validation_summary"]
    candidate.conflict_summary = snapshot["conflict_summary"]
    db.session.commit()
    return jsonify(_promotion_candidate_to_json(candidate, include_attempts=True)), 201


@api_bp.route("/projects/<uuid:project_id>/ship/candidates/<uuid:candidate_id>", methods=["GET"])
def ship_candidate_detail(project_id, candidate_id):
    """Fetch one stored promotion candidate snapshot."""
    _get_project_or_404(project_id)
    candidate = PromotionCandidate.query.filter_by(
        project_id=project_id, id=candidate_id
    ).first_or_404()
    payload = _promotion_candidate_to_json(candidate, include_attempts=True)
    latest_run = (
        ShipRun.query
        .filter_by(project_id=project_id, promotion_candidate_id=candidate_id)
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    payload["latest_ship_run"] = _ship_run_detail_payload(latest_run) if latest_run else None
    payload["membership"] = _candidate_membership_payload(candidate)
    payload["validation_errors"] = _validate_promotion_candidate(candidate, db.session.get(Project, project_id))
    return jsonify(payload)


@api_bp.route("/projects/<uuid:project_id>/ship/candidates/<uuid:candidate_id>/compose", methods=["POST"])
def ship_candidate_compose(project_id, candidate_id):
    project = _lock_project_for_update(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    if (getattr(project, "git_mode", None) or "swarm") != "swarm":
        return jsonify({"error": "Project is not in swarm mode"}), 400

    candidate = PromotionCandidate.query.filter_by(
        project_id=project_id, id=candidate_id
    ).first_or_404()

    existing = (
        ShipRun.query
        .filter_by(project_id=project_id, promotion_candidate_id=candidate_id)
        .filter(ShipRun.status.in_(_ACTIVE_SHIP_RUN_STATUSES))
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    if existing:
        return jsonify(_ship_run_detail_payload(existing)), 200

    validation_errors = _validate_promotion_candidate(candidate, project)
    if validation_errors:
        return jsonify({
            "error": "Candidate composition validation failed.",
            "details": validation_errors,
            "candidate": _promotion_candidate_to_json(candidate, include_attempts=True),
        }), 409

    run = ShipRun(
        project_id=str(project_id),
        promotion_candidate_id=str(candidate.id),
        wave_num=_candidate_legacy_wave_num(candidate),
        status="queued",
    )
    db.session.add(run)
    db.session.commit()
    current_app.logger.info("Compose queued for candidate %s project %s run %s", candidate_id, project_id, run.id)
    return jsonify(_ship_run_detail_payload(run)), 201


@api_bp.route("/projects/<uuid:project_id>/ship/candidates/<uuid:candidate_id>/run", methods=["GET"])
def ship_candidate_run(project_id, candidate_id):
    _get_project_or_404(project_id)
    candidate = PromotionCandidate.query.filter_by(
        project_id=project_id, id=candidate_id
    ).first_or_404()
    run = (
        ShipRun.query
        .filter_by(project_id=project_id, promotion_candidate_id=candidate.id)
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    if not run:
        return jsonify({
            "candidate": _promotion_candidate_to_json(candidate, include_attempts=True),
            "ship_run": None,
        }), 200
    return jsonify({
        "candidate": _promotion_candidate_to_json(candidate, include_attempts=True),
        "ship_run": _ship_run_detail_payload(run),
    })


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>", methods=["GET"])
def ship_wave_detail(project_id, wave_num):
    """Full legacy wave detail: tickets, accepted attempts, ship run, staleness."""
    _get_project_or_404(project_id)
    return jsonify(_wave_detail(project_id, wave_num))


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/dry-compose", methods=["GET", "POST"])
def ship_wave_dry_compose(project_id, wave_num):
    """Read-only compose preview with blockers, commit hashes, and safe next actions."""
    project = _get_project_or_404(project_id)
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    analysis = _analyze_wave_dependencies(tickets)
    waves = analysis["waves"]
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == wave_num]
    if not wave_tickets:
        return jsonify({"error": f"No tickets in wave {wave_num}"}), 404

    accepted_attempts = []
    missing_ticket_ids = []
    for ticket in wave_tickets:
        attempt = _get_accepted_attempt(ticket.id)
        if attempt:
            accepted_attempts.append(attempt)
        else:
            missing_ticket_ids.append(str(ticket.id))

    detail = _wave_detail(project_id, wave_num)
    ship_run = detail.get("ship_run") or {}

    return jsonify({
        "wave_num": wave_num,
        "safe_to_compose": detail["can_compose"],
        "all_done": detail["all_done"],
        "blockers": detail["blockers"],
        "next_actions": detail["next_actions"],
        "validation": detail["validation"],
        "shipped_frontier": detail["shipped_frontier"],
        "stale_details": detail["stale_details"],
        "commit_hashes": [a.agenthub_commit_hash for a in accepted_attempts if a.agenthub_commit_hash],
        "changed_files": ship_run.get("changed_files") or [],
        "existing_ship_run": ship_run or None,
        "missing_ticket_ids": missing_ticket_ids,
        "tickets": detail["tickets"],
        "dependency_cycles": analysis["dependency_cycles"],
        "unknown_dependency_refs": analysis["unknown_dependency_refs"],
    })


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/diff", methods=["GET"])
def ship_wave_diff(project_id, wave_num):
    """Return a best-effort diff preview for a composed wave."""
    project = _get_project_or_404(project_id)
    max_bytes_raw = (request.args.get("max_bytes") or "").strip()
    try:
        max_bytes = max(256, min(int(max_bytes_raw), 200_000)) if max_bytes_raw else 20_000
    except ValueError:
        max_bytes = 20_000

    detail = _wave_detail(project_id, wave_num)
    run = detail.get("ship_run") or {}
    if not run:
        return jsonify({
            "error": "No ship run exists for this candidate set. Review or compose the candidate first.",
            "next_actions": detail["next_actions"],
        }), 409
    if not run.get("composed_commit_hash"):
        return jsonify({
            "error": "Ship run is not composed yet. Compose the candidate first.",
            "next_actions": detail["next_actions"],
        }), 409

    changed_files = run.get("changed_files") or []
    diff_text = None
    truncated = False
    diff_note = None
    project_path = getattr(project, "project_path", None) or ""
    base_hash = run.get("base_main_hash")
    head_hash = run.get("composed_commit_hash")
    if project_path and os.path.isdir(project_path) and base_hash and head_hash:
        try:
            cat = subprocess.run(
                ["git", "cat-file", "-e", head_hash],
                cwd=project_path, capture_output=True, text=True, timeout=10,
            )
            if cat.returncode == 0:
                diff_proc = subprocess.run(
                    ["git", "diff", "--stat", "--summary", "--find-renames", base_hash, head_hash],
                    cwd=project_path, capture_output=True, text=True, timeout=20,
                )
                if diff_proc.returncode == 0:
                    diff_text = diff_proc.stdout
                    if len(diff_text.encode()) > max_bytes:
                        encoded = diff_text.encode()[:max_bytes]
                        diff_text = encoded.decode(errors="ignore")
                        truncated = True
            else:
                diff_note = "Composed commit is not available in the local repo; returning metadata only."
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            diff_note = f"Diff text unavailable: {exc}"
    else:
        diff_note = "Project path or compose base is unavailable; returning metadata only."

    return jsonify({
        "wave_num": wave_num,
        "base_hash": base_hash,
        "composed_commit_hash": head_hash,
        "changed_files": changed_files,
        "diff": diff_text,
        "truncated": truncated,
        "max_bytes": max_bytes,
        "note": diff_note,
        "next_actions": detail["next_actions"],
        "blockers": detail["blockers"],
    })


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/compose", methods=["POST"])
def ship_wave_compose(project_id, wave_num):
    """Queue a legacy wave-keyed ShipRun.

    Phase 1 contract: the long-term model is "select promotion candidate, then
    create ShipRun from that stable candidate set". This endpoint remains a
    compatibility surface until candidate-backed routes replace it.
    """
    project = _lock_project_for_update(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    if (getattr(project, "git_mode", None) or "swarm") != "swarm":
        return jsonify({"error": "Project is not in swarm mode"}), 400
    existing = (
        ShipRun.query
        .filter_by(project_id=project_id, wave_num=wave_num)
        .filter(ShipRun.status.in_(_ACTIVE_SHIP_RUN_STATUSES))
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    candidate = None
    if existing:
        candidate, _snapshot, _wave_tickets, error = _ensure_wave_candidate(project, wave_num)
        if not error and not existing.promotion_candidate_id and candidate is not None:
            existing.promotion_candidate_id = str(candidate.id)
            db.session.commit()
        return jsonify(_ship_run_detail_payload(existing)), 200

    candidate, snapshot, wave_tickets, error = _ensure_wave_candidate(project, wave_num)
    if error:
        return jsonify({"error": error}), 409 if "accepted" in error.lower() else 404

    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)
    validation_errors = _validate_wave_composition(project, wave_num, tickets, waves, wave_tickets)
    if validation_errors:
        return jsonify({
            "error": "Wave composition validation failed.",
            "details": validation_errors,
            "hint": "Ship prerequisite promotion work first, then recompose this candidate from the current frontier.",
        }), 409

    if snapshot and snapshot["status"] == "blocked":
        return jsonify({
            "error": "Wave composition validation failed.",
            "details": snapshot["validation_summary"].get("blockers", []),
            "hint": "Ship prerequisite promotion work first, then recompose this candidate from the current frontier.",
        }), 409

    run = ShipRun(
        project_id=str(project_id),
        promotion_candidate_id=str(candidate.id),
        wave_num=wave_num,
        status="queued",
    )
    db.session.add(run)
    db.session.commit()
    current_app.logger.info("Compose queued for wave %d via candidate %s project %s run %s", wave_num, candidate.id, project_id, run.id)
    return jsonify(_ship_run_detail_payload(run)), 201


def _ship_run_ship_response(project, run, *, merge_method: str = "merge"):
    slug = _repo_slug_from_github_url(project.github_url) if project.github_url else None

    gate, gate_status = _evidence_gate_response(project, "ship_run", run.id)
    if gate:
        return jsonify(gate), gate_status

    validation_errors = list(_ship_run_context(run)["validation_errors"])
    current_frontier = getattr(project, "shipped_frontier", None) or None
    if run.base_main_hash and current_frontier and run.base_main_hash != current_frontier:
        validation_errors.append(
            f"Ship run base {run.base_main_hash[:12]} is not the current frontier {current_frontier[:12]}."
        )
    if validation_errors:
        return jsonify({
            "error": "Ship run validation failed.",
            "details": validation_errors,
            "hint": "Recompose this ship run from the current frontier.",
        }), 409

    use_github = bool(slug and run.release_pr_number)
    if not use_github and not run.composed_commit_hash:
        return jsonify({
            "error": "Ship run has no composed commit hash. Recompose before shipping.",
        }), 409

    run.status = "shipping"
    db.session.commit()

    new_tip = None
    if use_github:
        try:
            r_check = subprocess.run(
                [
                    "gh", "pr", "view", str(run.release_pr_number),
                    "--json", "state,mergedAt,headRefName,headRefOid",
                    "-R", slug,
                ],
                capture_output=True, text=True, timeout=15, env=_env_for_gh_user(),
            )
            if r_check.returncode == 0:
                pr_state = json.loads(r_check.stdout or "{}")
                state = (pr_state.get("state") or "").upper()
                head_oid = pr_state.get("headRefOid") or ""
                if state == "MERGED":
                    new_tip = head_oid or run.composed_commit_hash
                    if not new_tip:
                        for branch in ("main", "master"):
                            r_tip = subprocess.run(
                                ["gh", "api", f"repos/{slug}/git/refs/heads/{branch}"],
                                capture_output=True, text=True, timeout=15, env=_env_for_gh_user(),
                            )
                            if r_tip.returncode == 0:
                                ref_data = json.loads(r_tip.stdout or "{}")
                                new_tip = (ref_data.get("object") or {}).get("sha") or None
                                if new_tip:
                                    break
                    return _finalize_shipped_run(project, run, new_tip=new_tip, root_refresh_source="release_pr_reconcile")
                if state == "CLOSED":
                    run.status = "ready_to_ship"
                    db.session.commit()
                    return jsonify({"error": "Release PR was closed without merging. Recompose the ship run."}), 409
                head_ref = pr_state.get("headRefName") or ""
                if run.release_branch and head_ref and head_ref != run.release_branch:
                    run.status = "ready_to_ship"
                    db.session.commit()
                    return jsonify({
                        "error": "Release PR branch does not match this ship run. Recompose the ship run.",
                        "expected_branch": run.release_branch,
                        "actual_branch": head_ref,
                    }), 409
                if run.composed_commit_hash and head_oid and head_oid != run.composed_commit_hash:
                    run.status = "ready_to_ship"
                    db.session.commit()
                    return jsonify({
                        "error": "Release PR head does not match the composed commit. Recompose the ship run.",
                        "expected_head": run.composed_commit_hash,
                        "actual_head": head_oid,
                    }), 409
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        if merge_method not in ("merge", "squash", "rebase"):
            merge_method = "merge"
        try:
            result = subprocess.run(
                ["gh", "pr", "merge", str(run.release_pr_number), f"--{merge_method}", "-R", slug],
                capture_output=True, text=True, timeout=60, env=_env_for_gh_user(),
            )
            if result.returncode != 0:
                run.status = "ready_to_ship"
                run.error = (result.stderr or result.stdout or "")[:2000]
                db.session.commit()
                payload, status_code = _ship_error_payload(
                    project,
                    run,
                    detail=run.error,
                    hint=f"GitHub merge failed in backend runtime. Run ta ship doctor {project.id} before retrying.",
                    phase="merge",
                )
                return jsonify(payload), status_code
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            run.status = "ready_to_ship"
            run.error = str(exc)[:2000]
            db.session.commit()
            payload, status_code = _ship_error_payload(
                project,
                run,
                detail=str(exc),
                hint=f"Backend runtime could not invoke gh. Run ta ship doctor {project.id} before retrying.",
                phase="merge",
            )
            return jsonify(payload), status_code

        for branch in ("main", "master"):
            r_tip = subprocess.run(
                ["gh", "api", f"repos/{slug}/git/refs/heads/{branch}"],
                capture_output=True, text=True, timeout=15, env=_env_for_gh_user(),
            )
            if r_tip.returncode == 0:
                ref_data = json.loads(r_tip.stdout or "{}")
                new_tip = (ref_data.get("object") or {}).get("sha") or None
                if new_tip:
                    break
    else:
        new_tip = run.composed_commit_hash
        current_app.logger.info(
            "ship_run_ship: no GitHub URL or PR — advancing frontier directly from composed hash %s",
            (new_tip or "")[:12],
        )
    return _finalize_shipped_run(project, run, new_tip=new_tip, root_refresh_source="release_pr_merge")


@api_bp.route("/projects/<uuid:project_id>/ship/runs/<uuid:run_id>", methods=["GET"])
def ship_run_detail(project_id, run_id):
    _get_project_or_404(project_id)
    run = ShipRun.query.filter_by(project_id=project_id, id=run_id).first_or_404()
    return jsonify(_ship_run_detail_payload(run))


@api_bp.route("/projects/<uuid:project_id>/ship/runs/<uuid:run_id>/ship", methods=["POST"])
def ship_run_ship(project_id, run_id):
    project = _lock_project_for_update(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    run = ShipRun.query.filter_by(project_id=project_id, id=run_id, status="ready_to_ship").first()
    if not run:
        return jsonify({"error": "No ship run in ready_to_ship state for this run id"}), 409
    merge_method = (request.json or {}).get("merge_method") or "merge"
    return _ship_run_ship_response(project, run, merge_method=str(merge_method).strip().lower())


@api_bp.route("/projects/<uuid:project_id>/ship/candidates/<uuid:candidate_id>/ship", methods=["POST"])
def ship_candidate_ship(project_id, candidate_id):
    project = _lock_project_for_update(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    candidate = PromotionCandidate.query.filter_by(
        project_id=project_id, id=candidate_id
    ).first_or_404()
    run = (
        ShipRun.query
        .filter_by(project_id=project_id, promotion_candidate_id=candidate.id, status="ready_to_ship")
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    if not run:
        return jsonify({"error": "No ship run in ready_to_ship state for this candidate"}), 409
    merge_method = (request.json or {}).get("merge_method") or "merge"
    return _ship_run_ship_response(project, run, merge_method=str(merge_method).strip().lower())


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/ship", methods=["POST"])
def ship_wave_ship(project_id, wave_num):
    """Advance the shipped_frontier for this wave and mark attempts shipped.

    Two paths (GitHub is optional):
      - With github_url + release_pr_number: merge the release PR via gh, then advance.
      - Without (local-mode or no-main): advance frontier directly from composed_commit_hash.

    This is a legacy wave-keyed ship surface. The target contract is shipping a
    ShipRun created from a stable promotion candidate.
    """
    project = _lock_project_for_update(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    run = (
        ShipRun.query
        .filter_by(project_id=project_id, wave_num=wave_num, status="ready_to_ship")
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    if not run:
        return jsonify({"error": "No ship run in ready_to_ship state for this wave"}), 409
    merge_method = (request.json or {}).get("merge_method") or "merge"
    return _ship_run_ship_response(project, run, merge_method=str(merge_method).strip().lower())


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/timeline", methods=["GET"])
def ship_wave_timeline(project_id, wave_num):
    """Aggregate AgentHub channel posts for a wave into a chronological timeline.
    Fetches the wave channel + all ticket channels for tickets in this wave."""
    project = _get_project_or_404(project_id)
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == wave_num]

    posts = []

    # Wave channel
    wave_ch = _wave_channel(project.name, wave_num)
    for p in _fetch_channel_posts(wave_ch, limit=100):
        posts.append({**_parse_event_post(p), "_channel": wave_ch, "_channel_type": "wave"})

    # Per-ticket channels
    for t in wave_tickets:
        ch = _ticket_channel(str(t.id))
        for p in _fetch_channel_posts(ch, limit=50):
            posts.append({
                **_parse_event_post(p),
                "_channel": ch,
                "_channel_type": "ticket",
                "_ticket_title": t.title,
                "_ticket_id": str(t.id),
            })

    # Sort chronologically (created_at string is ISO — lexicographic sort works)
    posts.sort(key=lambda p: p.get("created_at") or "")

    return jsonify(posts)


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/feedback", methods=["POST"])
def ship_wave_feedback(project_id, wave_num):
    """Post feedback to the wave's AgentHub channel. Body: { message, target_ticket_id (optional) }."""
    _get_project_or_404(project_id)
    data = request.json or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    project = db.session.get(Project, project_id)
    target_ticket = (data.get("target_ticket_id") or "").strip()
    if target_ticket:
        channel = _ticket_channel(target_ticket)
    else:
        project_name = project.name if project else str(project_id)
        channel = _wave_channel(project_name, wave_num)
    _post_event(
        channel,
        _event_content(
            "human_feedback",
            message,
            {"wave_num": wave_num, "target_ticket_id": target_ticket or None},
        ),
    )

    return jsonify({"message": "Feedback recorded"})


# ---------------------------------------------------------------------------
# Worker-facing merge routes (coordinator claims and executes merge runs)
# ---------------------------------------------------------------------------

def _collect_wave_commit_hashes(wave_tickets: list, project) -> list:
    """Collect AgentHub commit hashes for a set of wave tickets from ticket_attempts."""
    hashes = []
    for t in wave_tickets:
        attempt = (
            TicketAttempt.query
            .filter_by(ticket_id=t.id)
            .filter(TicketAttempt.status.in_(_SATISFIED_STATUSES))
            .order_by(TicketAttempt.attempt_num.desc())
            .first()
        )
        if attempt and attempt.agenthub_commit_hash:
            hashes.append(attempt.agenthub_commit_hash)
    return hashes


def _validate_wave_composition(
    project,
    wave_num: int,
    tickets: list,
    waves: dict,
    wave_tickets: list,
    *,
    analysis: dict | None = None,
) -> list[str]:
    """Return blocking validation errors for composing/shipping a legacy wave.

    ShipRun composition only receives the selected wave's leaves. Dependencies
    in earlier waves must therefore already be shipped into the project frontier;
    otherwise a later-wave release can silently include or omit parent work.

    Phase 1 note: the target validation contract is candidate-based DAG closure,
    not wave ordering. This helper remains only for the current compatibility
    path.
    """
    ticket_by_id = {str(t.id): t for t in tickets}
    frontier = getattr(project, "shipped_frontier", None) or None
    errors: list[str] = []
    analysis = analysis or _analyze_wave_dependencies(tickets)

    accepted_by_ticket = {
        str(t.id): _get_accepted_attempt(t.id)
        for t in wave_tickets
    }
    accepted_hashes = {
        a.agenthub_commit_hash
        for a in accepted_by_ticket.values()
        if a and a.agenthub_commit_hash
    }

    for ticket in wave_tickets:
        explanation = analysis["ticket_explanations"].get(str(ticket.id), {})
        errors.extend(explanation.get("blockers", []))
        attempt = accepted_by_ticket.get(str(ticket.id))
        if not attempt:
            errors.append(f"Ticket '{ticket.title[:40]}' has no accepted attempt.")
            continue
        if not attempt.agenthub_commit_hash:
            errors.append(f"Ticket '{ticket.title[:40]}' has no AgentHub commit hash.")

        if frontier and attempt.base_hash:
            allowed_bases = {frontier} | accepted_hashes
            if attempt.base_hash not in allowed_bases:
                errors.append(
                    f"Ticket '{ticket.title[:40]}' attempt base {attempt.base_hash[:12]} "
                    "is not the current frontier or a selected same-wave leaf."
                )

        for dep_id in ticket.depends_on_ticket_ids or []:
            dep = ticket_by_id.get(str(dep_id))
            if not dep:
                errors.append(f"Ticket '{ticket.title[:40]}' depends on unknown ticket {dep_id}.")
                continue
            dep_wave = waves.get(str(dep.id), 0)
            if dep_wave >= wave_num:
                errors.append(
                    f"Ticket '{ticket.title[:40]}' depends on '{dep.title[:40]}' "
                    "which is not in an earlier wave."
                )
                continue
            dep_attempt = _get_accepted_attempt(dep.id)
            if not dep_attempt:
                errors.append(
                    f"Ticket '{ticket.title[:40]}' depends on '{dep.title[:40]}' "
                    "which has no accepted attempt."
                )
                continue
            if dep_attempt.status != "shipped":
                errors.append(
                    f"Ticket '{ticket.title[:40]}' depends on '{dep.title[:40]}' "
                    f"which is {dep_attempt.status}, not shipped."
                )

    deduped: list[str] = []
    for error in errors:
        if error and error not in deduped:
            deduped.append(error)
    return deduped


@api_bp.route("/worker/ship-run/next", methods=["POST"])
def worker_ship_run_next():
    """Coordinator claims the next queued merge run.
    Returns 204 if nothing to do, or candidate-backed run context."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status

    run = (
        ShipRun.query.filter_by(status="queued")
        .order_by(ShipRun.wave_num.asc(), ShipRun.created_at.asc(), ShipRun.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not run:
        return "", 204

    run.status = "composing"
    db.session.commit()

    context = _ship_run_context(run)
    project = context["project"]
    if project:
        _post_event(
            _wave_channel(project.name, context["wave_num"]),
            _event_content(
                "release_composition_started",
                f"Release composition started for wave {context['wave_num']}",
                {
                    "wave_num": context["wave_num"],
                    "ship_run_id": str(run.id),
                    "promotion_candidate_id": str(run.promotion_candidate_id) if run.promotion_candidate_id else None,
                },
            ),
        )

    return jsonify({
        "run": _ship_run_to_json(run),
        "project": {
            "id": str(project.id),
            "name": project.name,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "git_mode": project.git_mode,
        },
        "candidate": _promotion_candidate_to_json(context["candidate"], include_attempts=True) if context["candidate"] else None,
        "membership": context["membership"],
        "wave_tickets": context["wave_tickets"],
        "commit_hashes": context["commit_hashes"],
    }), 200


@api_bp.route("/worker/ship-run/<uuid:run_id>", methods=["GET"])
def worker_ship_run_get(run_id):
    """Fetch full data for a specific (already-claimed) merge run.
    Used by coordinator-spawned merger subprocesses that were pre-claimed via /worker/ship-run/next."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = ShipRun.query.filter_by(id=run_id).first_or_404()
    context = _ship_run_context(run)
    project = context["project"]
    return jsonify({
        "run": _ship_run_to_json(run),
        "project": {
            "id": str(project.id),
            "name": project.name,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "git_mode": project.git_mode,
        },
        "candidate": _promotion_candidate_to_json(context["candidate"], include_attempts=True) if context["candidate"] else None,
        "membership": context["membership"],
        "wave_tickets": context["wave_tickets"],
        "commit_hashes": context["commit_hashes"],
    }), 200


@api_bp.route("/worker/ship-run/<uuid:run_id>/composed", methods=["POST"])
def worker_ship_run_composed(run_id):
    """Legacy-compatible shipper callback for successful composition: release branch created,
    PR opened, ready to ship. Body: release_branch, release_pr_url, release_pr_number,
    composed_commit_hash, base_main_hash,
          test_status, test_output, changed_files, summary."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = ShipRun.query.filter_by(id=run_id).first_or_404()
    data = request.json or {}
    run.status = "ready_to_ship"
    run.release_branch = (data.get("release_branch") or "").strip() or None
    run.release_pr_url = (data.get("release_pr_url") or "").strip() or None
    run.release_pr_number = data.get("release_pr_number") or None
    run.composed_commit_hash = (data.get("composed_commit_hash") or "").strip() or None
    run.base_main_hash = (data.get("base_main_hash") or "").strip() or None
    run.test_status = (data.get("test_status") or "").strip() or None
    run.test_output = (data.get("test_output") or "")[:8000] or None
    run.changed_files = data.get("changed_files") or []
    run.summary = (data.get("summary") or "").strip() or None
    candidate = db.session.get(PromotionCandidate, run.promotion_candidate_id) if run.promotion_candidate_id else None
    if candidate is not None:
        candidate.status = "composed"
        candidate.composed_commit_hash = run.composed_commit_hash
    db.session.commit()
    context = _ship_run_context(run)
    current_app.logger.info(
        "Ship run %s composed for wave %d: PR #%s branch %s",
        run_id, context["wave_num"], run.release_pr_number, run.release_branch,
    )
    project = db.session.get(Project, run.project_id)
    if project:
        pr_ref = f"PR #{run.release_pr_number}" if run.release_pr_number else run.release_branch or "no PR"
        _post_event(
            _wave_channel(project.name, context["wave_num"]),
            _event_content(
                "release_pr_opened",
                f"{pr_ref} opened; tests={run.test_status or 'skipped'}; files={len(run.changed_files or [])}",
                {
                    "wave_num": context["wave_num"],
                    "ship_run_id": str(run.id),
                    "promotion_candidate_id": str(run.promotion_candidate_id) if run.promotion_candidate_id else None,
                    "release_pr_number": run.release_pr_number,
                    "release_pr_url": run.release_pr_url,
                    "release_branch": run.release_branch,
                    "test_status": run.test_status,
                    "base_main_hash": run.base_main_hash,
                    "composed_commit_hash": run.composed_commit_hash,
                    "changed_file_count": len(run.changed_files or []),
                },
            ),
        )
    return jsonify(_ship_run_detail_payload(run))


@api_bp.route("/worker/ship-run/<uuid:run_id>/fail", methods=["POST"])
def worker_ship_run_fail(run_id):
    """Shipper or merger reports failure. Body: error, fix_ticket_title, fix_ticket_description,
    compose_failed (bool). If compose_failed=true, status is set to compose_failed rather
    than failed for compatibility with the older callback flow."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = ShipRun.query.filter_by(id=run_id).first_or_404()
    data = request.json or {}
    run.status = "compose_failed" if data.get("compose_failed") else "failed"
    run.error = (data.get("error") or "")[:4000]
    db.session.commit()

    context = _ship_run_context(run)
    project = context["project"]
    if project:
        error_short = (run.error or "")[:200]
        _post_event(
            _wave_channel(project.name, context["wave_num"]),
            _event_content(
                "release_composition_failed",
                error_short or "Release composition failed",
                {
                    "wave_num": context["wave_num"],
                    "ship_run_id": str(run.id),
                    "promotion_candidate_id": str(run.promotion_candidate_id) if run.promotion_candidate_id else None,
                    "error": run.error,
                },
            ),
        )

    # Auto-create a fix ticket if the agent supplied one
    fix_title = (data.get("fix_ticket_title") or "").strip()
    fix_desc = (data.get("fix_ticket_description") or "").strip()
    fix_ticket_id = None
    if fix_title:
        fix = Ticket(
            project_id=run.project_id,
            title=fix_title,
            description=fix_desc or fix_title,
            column_id="backlog",
            priority="high",
            status="todo",
            associated_node_ids=["*"],
        )
        db.session.add(fix)
        db.session.commit()
        fix_ticket_id = str(fix.id)

    result = _ship_run_detail_payload(run)
    if fix_ticket_id:
        result["fix_ticket_id"] = fix_ticket_id
    return jsonify(result)


@api_bp.route("/worker/ship-run/reset-stale", methods=["POST"])
def worker_ship_run_reset_stale():
    """Reset ship runs stuck in 'composing' or legacy 'running' state after restart.
    Body: optional {"max_age_seconds": N} — reset runs older than N seconds (default 1800)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    try:
        max_age = int(data.get("max_age_seconds", 1800))
    except (TypeError, ValueError):
        max_age = 1800
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    stale = ShipRun.query.filter(
        ShipRun.status.in_(["composing", "running"]),
        ShipRun.updated_at < cutoff,
    ).all()
    count = len(stale)
    for run in stale:
        run.status = "queued"  # re-queue so coordinator picks it up again
        run.error = f"Reset by coordinator after {max_age}s stale timeout."
    db.session.commit()
    current_app.logger.info("Reset %d stale active ship run(s) (older than %ds)", count, max_age)
    return jsonify({"reset": count, "max_age_seconds": max_age})
