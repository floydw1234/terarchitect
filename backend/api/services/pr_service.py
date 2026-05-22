"""PR review domain helpers: GitHub polling, comment classification, and review-job enqueueing."""
import json
import os
import re
import subprocess
import time
from datetime import datetime

from flask import current_app
from sqlalchemy import nullslast, or_

from models.db import db, AgentJob, PR, PRReviewComment, Project, Ticket
from utils.app_settings import get_dashboard_git_env, get_gh_env_for_user

# Invisible HTML comment appended to all agent-posted PR comments.
# Used to distinguish agent replies from human reviewer comments during PR polling.
BOT_COMMENT_SIGNATURE = "<!-- terarchitect-bot -->"


def env_for_gh_user():
    """Env for gh CLI in UI context (PR comment, approve, merge, poll). Uses stored user token and dashboard git identity if set."""
    return {**os.environ, **get_gh_env_for_user(), **get_dashboard_git_env()}


def repo_slug_from_github_url(url):
    """Extract owner/repo from https://github.com/owner/repo or similar. Returns None if not parseable."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip().rstrip("/")
    if "github.com" not in url:
        return None
    path = url.split("github.com")[-1].strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        return None
    slug = "/".join(parts[:2])
    if not re.match(r'^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$', slug):
        return None
    return slug


def is_test_file(path):
    """True if path looks like a test file (by convention). Excludes __init__.py (package marker)."""
    if not path:
        return False
    path_norm = path.replace("\\", "/")
    path_lower = path_norm.lower()
    base = path_norm.split("/")[-1] if "/" in path_norm else path_norm
    base_lower = base.lower()
    if base_lower == "__init__.py":
        return False
    return (
        "__tests__" in path_lower
        or "/tests/" in path_lower
        or path_lower.endswith("_test.py")
        or (base_lower.startswith("test_") and base_lower.endswith(".py"))
        or path_lower.endswith("_test.go")
        or path_lower.endswith("_test.js")
        or ".test." in path_lower
        or ".spec." in path_lower
        or path_lower.endswith(".test.js")
        or path_lower.endswith(".test.jsx")
        or path_lower.endswith(".test.ts")
        or path_lower.endswith(".test.tsx")
        or path_lower.endswith(".spec.js")
        or path_lower.endswith(".spec.jsx")
    )


def extract_test_names_from_patch(patch):
    """From a unified diff patch, extract test/spec names from added lines. Returns list of unique strings."""
    if not patch:
        return []
    seen = set()
    out = []
    # Match it('...'), it("..."), test('...'), test("..."), describe('...')
    for m in re.finditer(
        r"""(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)['"`]""",
        patch,
        re.IGNORECASE,
    ):
        name = m.group(1).strip()
        if name and name not in seen and len(name) < 200:
            seen.add(name)
            out.append(name)
    # Match def test_something(
    for m in re.finditer(r"^\+\s*def\s+(test_\w+)\s*\(", patch, re.MULTILINE):
        name = m.group(1).strip()
        name = name.replace("_", " ").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def is_approval_comment(body: str) -> bool:
    """LLM-based check: returns True when the comment is a pure approval and the agent should NOT run.
    Delegates to utils.pr_comment_classifier; falls back to False on any error."""
    try:
        from utils.app_settings import get_frontend_llm_settings
        from utils.pr_comment_classifier import classify_comment_is_approval
        result = classify_comment_is_approval(body, get_frontend_llm_settings())
        current_app.logger.info("Approval check for comment (%.60s...): %s", body, result)
        return result
    except Exception as e:
        current_app.logger.warning("Approval comment check failed (%s); defaulting to trigger agent", e)
        return False


def enqueue_review_job(ticket_id, comment_body, pr_number, project_id, github_comment_id):
    """Enqueue a PR review job to agent_jobs. Skip if same ticket+PR already pending/running."""
    existing = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.kind == "review",
        AgentJob.pr_number == pr_number,
        AgentJob.status.in_(["pending", "running"]),
    ).with_for_update(skip_locked=True).first()
    if existing:
        current_app.logger.info("Skipping enqueue: ticket %s PR #%s already has job", ticket_id, pr_number)
        return
    db.session.add(AgentJob(
        ticket_id=ticket_id,
        project_id=project_id,
        kind="review",
        status="pending",
        pr_number=pr_number,
        comment_body=comment_body,
        github_comment_id=github_comment_id,
    ))
    db.session.commit()
    current_app.logger.info("Enqueued review job for ticket %s PR #%s", ticket_id, pr_number)


def mark_pr_comment_addressed(project_id, pr_number, github_comment_id):
    """Mark a PR comment as addressed (we replied). Call with app context."""
    row = PRReviewComment.query.filter_by(
        project_id=project_id,
        pr_number=pr_number,
        github_comment_id=github_comment_id,
    ).first()
    if row:
        row.addressed_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        try:
            db.session.commit()
            current_app.logger.info("Marked PR comment %s (PR #%s) as addressed", github_comment_id, pr_number)
        except Exception:
            db.session.rollback()


def split_paginate_output(stdout: str) -> list:
    """Parse `gh api --paginate` output into a list of JSON values.
    gh --paginate writes one JSON array per page, concatenated without a separator.
    This splits them by scanning for array boundaries and returns a flat list of all items."""
    results = []
    text = stdout.strip()
    i = 0
    while i < len(text):
        if text[i] != "[":
            i += 1
            continue
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "[":
                depth += 1
            elif text[j] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[i:j + 1])
                        results.append(parsed)
                    except json.JSONDecodeError:
                        pass
                    i = j + 1
                    break
            j += 1
        else:
            break
    return results if results else [json.loads(text)]


def get_ticket_pr_slug(project_id, ticket_id):
    """Return (pr_row, slug) for ticket's PR, or (None, None). 404 if ticket/project missing."""
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    project = Project.query.get_or_404(project_id)
    pr_row = PR.query.filter_by(ticket_id=ticket.id).first()
    if not pr_row or not pr_row.pr_number:
        return None, None
    slug = repo_slug_from_github_url(project.github_url)
    return pr_row, slug


def poll_pr_review_comments():
    """Check PRs in review for new comments via gh CLI and trigger review agent for new ones. Call with app context."""
    # Tickets in_review with a PR
    prs_in_review = list(
        db.session.query(PR, Ticket, Project)
        .join(Ticket, Ticket.id == PR.ticket_id)
        .join(Project, Project.id == PR.project_id)
        .filter(Ticket.column_id == "in_review")
        .filter(Project.github_url.isnot(None))
        .filter(PR.pr_number.isnot(None))
        .filter(db.or_(Project.git_mode == "structured", Project.git_mode.is_(None)))
        .all()
    )
    if prs_in_review:
        current_app.logger.info("PR review poll: checking %d PR(s) for new comments", len(prs_in_review))
    for pr_row, ticket, project in prs_in_review:
        slug = repo_slug_from_github_url(project.github_url)
        if not slug:
            continue
        pr_number = pr_row.pr_number

        # Check if PR was merged -> move ticket to done
        try:
            r_pr = subprocess.run(
                ["gh", "api", f"repos/{slug}/pulls/{pr_number}"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env_for_gh_user(),
            )
            if r_pr.returncode == 0 and r_pr.stdout:
                pr_data = json.loads(r_pr.stdout)
                if pr_data.get("merged"):
                    ticket.column_id = "done"
                    ticket.status = "completed"
                    try:
                        db.session.commit()
                        current_app.logger.info(
                            "PR #%s merged; moved ticket %s to done",
                            pr_number,
                            ticket.id,
                        )
                    except Exception:
                        db.session.rollback()
                    continue  # Skip approval check and comment processing
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        # Check if PR was approved (per-reviewer latest state is APPROVED, no blocking CHANGES_REQUESTED) -> move ticket to done
        try:
            r_reviews = subprocess.run(
                ["gh", "api", f"repos/{slug}/pulls/{pr_number}/reviews", "--paginate"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env_for_gh_user(),
            )
            if r_reviews.returncode == 0 and r_reviews.stdout:
                # gh --paginate writes one JSON array per page concatenated; flatten into one list.
                raw_stdout = r_reviews.stdout.strip()
                reviews: list = []
                for chunk in split_paginate_output(raw_stdout):
                    if isinstance(chunk, list):
                        reviews.extend(chunk)
                if reviews:
                    # Build per-reviewer latest state; APPROVED only if no reviewer has CHANGES_REQUESTED pending
                    latest_by_reviewer: dict = {}
                    for rev in reviews:
                        login = (rev.get("user") or {}).get("login") or "unknown"
                        state = rev.get("state") or ""
                        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                            latest_by_reviewer[login] = state
                    has_approval = any(s == "APPROVED" for s in latest_by_reviewer.values())
                    has_blocking = any(s == "CHANGES_REQUESTED" for s in latest_by_reviewer.values())
                    if has_approval and not has_blocking:
                        ticket.column_id = "done"
                        ticket.status = "completed"
                        try:
                            db.session.commit()
                            current_app.logger.info(
                                "PR #%s approved; moved ticket %s to done",
                                pr_number,
                                ticket.id,
                            )
                        except Exception:
                            db.session.rollback()
                        continue  # Skip comment processing for this PR
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        # Issue comments, line (review) comments, and PR review submissions (e.g. "Submit review" with body)
        raw_comments = []
        for endpoint in (
            f"repos/{slug}/issues/{pr_number}/comments",
            f"repos/{slug}/pulls/{pr_number}/comments",
            f"repos/{slug}/pulls/{pr_number}/reviews",
        ):
            try:
                r = subprocess.run(
                    ["gh", "api", endpoint, "--paginate"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=env_for_gh_user(),
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                current_app.logger.warning("PR poll gh api failed %s: %s", endpoint, e)
                continue
            if r.returncode != 0:
                current_app.logger.warning(
                    "PR poll gh api non-zero %s: code=%s stderr=%s",
                    endpoint, r.returncode, (r.stderr or "").strip()[:200],
                )
                continue
            try:
                for chunk in split_paginate_output(r.stdout) if r.stdout else []:
                    if isinstance(chunk, list):
                        raw_comments.extend(chunk)
            except (json.JSONDecodeError, Exception):
                continue
        # Normalize and upsert into pr_review_comments (id, body, author_login, created_at)
        for c in raw_comments:
            cid = c.get("id")
            body = (c.get("body") or "").strip()
            if cid is None or not body:
                continue
            author = (c.get("user") or {}).get("login")
            created = c.get("created_at") or c.get("submitted_at")
            try:
                comment_ts = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
            except (ValueError, TypeError):
                comment_ts = None
            row = PRReviewComment.query.filter_by(
                project_id=project.id,
                pr_number=pr_number,
                github_comment_id=int(cid),
            ).first()
            if row:
                row.body = body
                row.author_login = author
                row.comment_created_at = comment_ts
                row.updated_at = datetime.utcnow()
            else:
                db.session.add(PRReviewComment(
                    project_id=project.id,
                    ticket_id=ticket.id,
                    pr_number=pr_number,
                    github_comment_id=int(cid),
                    author_login=author,
                    body=body,
                    comment_created_at=comment_ts,
                ))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            continue
        # Mark comments we should never respond to as addressed:
        #   1. Comments with our bot signature (agent's own replies).
        #   2. Comments posted by any GitHub bot account (login ends with "[bot]"),
        #      e.g. claude[bot], orca-security-us[bot], github-actions[bot].
        bot_comments = PRReviewComment.query.filter(
            PRReviewComment.project_id == project.id,
            PRReviewComment.pr_number == pr_number,
            PRReviewComment.addressed_at.is_(None),
            or_(
                PRReviewComment.body.contains(BOT_COMMENT_SIGNATURE),
                PRReviewComment.author_login.like("%[bot]"),
            ),
        ).all()
        for row in bot_comments:
            row.addressed_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
        if bot_comments:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        # Trigger only for the single most recent unaddressed human comment (no bot signature)
        next_comment = (
            PRReviewComment.query.filter(
                PRReviewComment.project_id == project.id,
                PRReviewComment.pr_number == pr_number,
                PRReviewComment.addressed_at.is_(None),
                PRReviewComment.body.isnot(None),
                PRReviewComment.body != "",
                ~PRReviewComment.body.contains(BOT_COMMENT_SIGNATURE),
            )
            .order_by(nullslast(PRReviewComment.comment_created_at.desc()))
            .limit(1)
            .first()
        )
        if next_comment:
            # Skip comments that are pure blockquotes (user forwarded a bot comment without
            # adding their own feedback — every non-empty line starts with '>').
            body_lines = [l for l in (next_comment.body or "").splitlines() if l.strip()]
            if body_lines and all(l.lstrip().startswith(">") for l in body_lines):
                next_comment.addressed_at = datetime.utcnow()
                next_comment.updated_at = datetime.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                current_app.logger.info(
                    "PR #%s comment %s is a pure quote-forward — skipping agent",
                    pr_number, next_comment.github_comment_id,
                )
            elif is_approval_comment(next_comment.body):
                # Pure approval — mark addressed and skip firing the agent.
                next_comment.addressed_at = datetime.utcnow()
                next_comment.updated_at = datetime.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                current_app.logger.info(
                    "PR #%s comment %s classified as approval — skipping agent",
                    pr_number, next_comment.github_comment_id,
                )
            else:
                enqueue_review_job(
                    ticket.id,
                    next_comment.body,
                    pr_number,
                    project.id,
                    next_comment.github_comment_id,
                )


def run_pr_poll_loop(app, pr_poll_seconds=60):
    """Background thread: run PR review comment poll; new comments enqueue to agent_jobs. No in-process agent run."""
    while True:
        time.sleep(pr_poll_seconds)
        try:
            with app.app_context():
                poll_pr_review_comments()
        except Exception as e:
            if app:
                app.logger.exception("PR review poller error: %s", e)
