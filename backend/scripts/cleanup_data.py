#!/usr/bin/env python3
"""
One-off data cleanup: fix duplicate PRs, wrong project_id, and orphans.
Run from backend dir: python -m scripts.cleanup_data
"""
import os
import sys
from pathlib import Path

# backend root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(Path(__file__).resolve().parent.parent)

from main import create_app
from models.db import db, PR, Ticket


def cleanup():
    app = create_app()
    with app.app_context():
        # 1) Fix PR.project_id to match ticket's project (if ticket exists)
        prs = PR.query.filter(PR.ticket_id.isnot(None)).all()
        fixed_project = 0
        for pr in prs:
            ticket = Ticket.query.get(pr.ticket_id)
            if ticket and str(pr.project_id) != str(ticket.project_id):
                pr.project_id = ticket.project_id
                fixed_project += 1
        if fixed_project:
            db.session.commit()
            print(f"Fixed PR.project_id for {fixed_project} row(s)")

        # 2) Delete orphaned PRs (no ticket or ticket missing)
        all_prs = PR.query.all()
        orphans = [pr for pr in all_prs if pr.ticket_id is None or Ticket.query.get(pr.ticket_id) is None]
        for pr in orphans:
            db.session.delete(pr)
        if orphans:
            db.session.commit()
            print(f"Deleted {len(orphans)} orphaned PR(s)")

        # 3) Per (project_id, pr_number), keep one PR; delete duplicates
        from sqlalchemy import func
        dupes = (
            db.session.query(PR.project_id, PR.pr_number, func.count(PR.id).label("n"))
            .filter(PR.pr_number.isnot(None))
            .group_by(PR.project_id, PR.pr_number)
            .having(func.count(PR.id) > 1)
            .all()
        )
        removed = 0
        for project_id, pr_number, _ in dupes:
            candidates = (
                PR.query.filter_by(project_id=project_id, pr_number=pr_number)
                .order_by(PR.created_at.asc())
                .all()
            )
            # Prefer the one whose ticket is in_review; else keep oldest
            tickets = {pr.ticket_id: Ticket.query.get(pr.ticket_id) for pr in candidates if pr.ticket_id}
            def keep_order(pr):
                t = tickets.get(pr.ticket_id)
                in_review = 0 if (t and t.column_id == "in_review") else 1
                return (in_review, pr.created_at or "")
            candidates.sort(key=keep_order)
            for pr in candidates[1:]:
                db.session.delete(pr)
                removed += 1
        if removed:
            db.session.commit()
            print(f"Removed {removed} duplicate PR(s) for same project+pr_number")

        if not (fixed_project or orphans or removed):
            print("No cleanup needed.")
        else:
            print("Cleanup done.")


if __name__ == "__main__":
    cleanup()
