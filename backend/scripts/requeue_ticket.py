"""
Reset a ticket so it can be queued again: move to backlog and mark any pending/running
agent jobs for that ticket as failed. After running, move the ticket to In Progress in the
UI to enqueue a new job.

Usage (from repo root):
  cd backend && python -m scripts.requeue_ticket "web UI refresh"
  # or set TICKET_TITLE:
  TICKET_TITLE="web UI refresh" python -m scripts.requeue_ticket
"""
from __future__ import print_function

import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def main():
    from main import create_app
    from models.db import db, Ticket, AgentJob

    title = (os.environ.get("TICKET_TITLE") or "").strip()
    if not title and len(sys.argv) > 1:
        title = (sys.argv[1] or "").strip()
    if not title:
        print("Usage: python -m scripts.requeue_ticket \"web UI refresh\"", file=sys.stderr)
        print("   or: TICKET_TITLE=\"web UI refresh\" python -m scripts.requeue_ticket", file=sys.stderr)
        return 2

    app = create_app()
    with app.app_context():
        tickets = Ticket.query.filter(Ticket.title.ilike("%" + title + "%")).all()
        if not tickets:
            print(f"No ticket matching title '{title}' found.", file=sys.stderr)
            return 1
        if len(tickets) > 1:
            print(f"Multiple tickets match; resetting all: {[t.title for t in tickets]}")

        for ticket in tickets:
            # Move ticket to backlog so user can move to In Progress again
            ticket.column_id = "backlog"
            ticket.status = "todo"

            # Mark any pending/running jobs for this ticket as failed so enqueue won't skip
            jobs = AgentJob.query.filter(
                AgentJob.ticket_id == ticket.id,
                AgentJob.status.in_(["pending", "running"]),
            ).all()
            for job in jobs:
                job.status = "failed"

            print(
                f"Reset ticket '{ticket.title}' (id={ticket.id}) to backlog; "
                f"marked {len(jobs)} job(s) as failed."
            )

        db.session.commit()
        print("Done. Move the ticket to In Progress in the UI to queue it again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
