#!/usr/bin/env python3
"""Dump execution logs for a ticket to a text file. Finds ticket by title (and optional column).
Usage: from repo root,
  python backend/scripts/dump_ticket_logs.py "web UI refresh" [--column in_progress] [--out path]
"""
import argparse
import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

from main import create_app
from models.db import db, ExecutionLog, Ticket


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump ticket execution logs to a text file")
    parser.add_argument("title", help="Ticket title to match (substring or exact)")
    parser.add_argument("--column", default=None, help="Optional: column_id filter (e.g. in_progress)")
    parser.add_argument("--out", "-o", default=None, help="Output file path (default: ticket_logs_<ticket_id>.txt)")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        q = Ticket.query.filter(Ticket.title.ilike(f"%{args.title}%"))
        if args.column:
            q = q.filter_by(column_id=args.column)
        tickets = q.all()
        if not tickets:
            print(f"No ticket found with title matching '{args.title}'" + (f" and column={args.column}" if args.column else ""), file=sys.stderr)
            sys.exit(1)
        if len(tickets) > 1:
            print(f"Multiple tickets matched; using first: {tickets[0].title!r} (id={tickets[0].id})", file=sys.stderr)
        ticket = tickets[0]
        project_id = ticket.project_id
        ticket_id = ticket.id

        logs = (
            ExecutionLog.query.filter_by(project_id=project_id, ticket_id=ticket_id)
            .order_by(ExecutionLog.created_at.asc())
            .all()
        )
        if not logs:
            print(f"No execution logs for ticket {ticket_id} ({ticket.title!r}).", file=sys.stderr)
            sys.exit(1)

        out_path = args.out or f"ticket_logs_{ticket_id}.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Execution logs: {ticket.title!r}\n")
            f.write(f"# Ticket ID: {ticket_id}\n")
            f.write(f"# Project ID: {project_id}\n")
            f.write(f"# Log entries: {len(logs)}\n")
            f.write("\n")
            for log in logs:
                created = log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else ""
                f.write(f"{'='*80}\n")
                f.write(f"[{created}] {log.step or ''}\n")
                if log.summary:
                    f.write(f"Summary: {log.summary}\n")
                if log.raw_output:
                    f.write("\n--- raw_output ---\n")
                    f.write(log.raw_output)
                    f.write("\n--- end raw_output ---\n")
                f.write("\n")
        print(f"Wrote {len(logs)} log entries to {os.path.abspath(out_path)}")


if __name__ == "__main__":
    main()
