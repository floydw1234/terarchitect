"""
One-off: create the default "Project setup" ticket for an existing project.
Usage: from backend/ with FLASK_APP=main.py and app context, or run with project id.
  PROJECT_ID=e96244aa-22c2-4da3-a765-b1d375c430e3 python -c "
  import os, sys, json
  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
  from main import app
  from models.db import db, Ticket
  from api.routes import _config_dir, _default_tickets_path
  "
  Or: flask shell, then run the block below.
"""
from __future__ import print_function

import json
import os
import sys

# Allow running from backend/ or repo root
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# Default project id to create setup ticket for
PROJECT_ID = os.environ.get("PROJECT_ID", "e96244aa-22c2-4da3-a765-b1d375c430e3")


def main():
    from main import create_app
    from models.db import db, Ticket, Project

    app = create_app()

    config_dir = os.path.join(_backend, "config")
    path = os.path.join(config_dir, "default_tickets.json")
    if not os.path.isfile(path):
        print("Config not found:", path)
        return 2
    with open(path, encoding="utf-8") as f:
        default_tickets = json.load(f)
    if not isinstance(default_tickets, list):
        print("Expected list in default_tickets.json")
        return 2

    with app.app_context():
        project = Project.query.get(PROJECT_ID)
        if not project:
            print("Project not found:", PROJECT_ID)
            return 1
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
        print("Created", len(default_tickets), "ticket(s) for project", PROJECT_ID)
    return 0


if __name__ == "__main__":
    sys.exit(main())
