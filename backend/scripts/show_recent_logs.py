#!/usr/bin/env python3
"""Print the most recent execution log entries (agent steps and outputs) for debugging."""
import os
import sys

# Run from backend so imports work
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

from main import create_app
from models.db import db, ExecutionLog, Project

def main():
    app = create_app()
    with app.app_context():
        n = int(os.environ.get("TERA_LOG_TAIL", "40"))
        logs = (
            ExecutionLog.query
            .order_by(ExecutionLog.created_at.desc())
            .limit(n)
            .all()
        )
        logs = list(reversed(logs))  # oldest first in the window
        if not logs:
            print("No execution logs found.")
            return
        print(f"Most recent {len(logs)} execution log entries (oldest first in window):\n")
        for log in logs:
            step = log.step or ""
            summary = (log.summary or "")[:200]
            raw = log.raw_output or ""
            raw_preview = raw[:1200] if raw else ""
            created = log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else ""
            print(f"[{created}] {step}")
            if summary:
                print(f"  summary: {summary}")
            if raw:
                print(f"  raw_output (len={len(raw)}, truncated):\n{raw_preview}")
                if len(raw) > 1200:
                    print("  ...")
            print()

        # If --trace: find latest session's trace file and print tail
        if "--trace" in sys.argv or os.environ.get("TERA_SHOW_TRACE"):
            latest = ExecutionLog.query.filter(
                ExecutionLog.session_id.isnot(None),
                ExecutionLog.session_id != "",
            ).order_by(ExecutionLog.created_at.desc()).first()
            if not latest or not latest.session_id:
                print("No session_id in logs, cannot locate trace file.")
                return
            proj = Project.query.get(latest.project_id)
            if not proj or not proj.project_path or not os.path.isdir(proj.project_path):
                print(f"Project path not set or missing: {getattr(proj, 'project_path', None)}")
                return
            trace_dir = os.path.join(proj.project_path, ".terarchitect")
            trace_path = os.path.join(trace_dir, f"middle_agent_{latest.session_id}.log")
            if not os.path.isfile(trace_path):
                print(f"Trace file not found: {trace_path}")
                return
            size = os.path.getsize(trace_path)
            tail_bytes = int(os.environ.get("TERA_TRACE_TAIL_BYTES", "50000"))
            with open(trace_path, "r", encoding="utf-8", errors="replace") as f:
                if size <= tail_bytes:
                    content = f.read()
                else:
                    f.seek(size - tail_bytes)
                    content = "(... truncated ...)\n" + f.read()
            print("\n" + "=" * 60 + "\nTrace log tail (agent prompts + worker outputs):\n" + "=" * 60 + "\n")
            print(content)

if __name__ == "__main__":
    main()
