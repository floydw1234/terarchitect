"""
Terarchitect Backend - Flask Application
"""
import re
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv(Path(__file__).resolve().parent / ".env")

from flask import Flask, jsonify
from flask_cors import CORS
from models.db import db

def create_app():
    app = Flask(__name__)

    # Configure CORS - allow localhost and any origins specified via CORS_ORIGINS env var.
    # CORS_ORIGINS is comma-separated. Special value "ANY_PORT_3000" allows the frontend
    # (port 3000) from any hostname, which covers local, LAN, port-forward access.
    _base_origins: list = [
        re.compile(r"http://localhost:\d+"),
        re.compile(r"http://127\.0\.0\.1:\d+"),
    ]
    _cors_env = (os.environ.get("CORS_ORIGINS") or "").strip()
    for entry in _cors_env.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if entry == "ANY_PORT_3000":
            _base_origins.append(re.compile(r"https?://.+:3000"))
        else:
            _base_origins.append(entry)
    CORS(app, resources={
        r"/api/*": {
            "origins": _base_origins,
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    })

    # Load configuration - DATABASE_URL for local run, SQLALCHEMY_DATABASE_URI overrides
    db_uri = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get(
        "DATABASE_URL",
        "postgresql://terarchitect:terarchitect@localhost:5433/terarchitect",
    )
    memory_save_dir = os.environ.get("MEMORY_SAVE_DIR", "/tmp/terarchitect")
    memory_save_dir = os.path.abspath(memory_save_dir)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
            "pool_recycle": 300,
        },
        MEMORY_SAVE_DIR=memory_save_dir,
    )

    db.init_app(app)
    with app.app_context():
        db.create_all()

    # Register blueprints
    from api import api_bp
    from api.embedding_openai import embedding_bp
    from api.services.pr_service import run_pr_poll_loop as _run_pr_poll_loop
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(embedding_bp)

    # Background thread: PR comment poll; new comments enqueue to agent_jobs. No in-process agent.
    # Use an app-level attribute to ensure only one polling thread starts per process even if
    # create_app() is called multiple times (e.g. during testing or Gunicorn preload).
    import threading
    if not getattr(app, "_pr_poll_started", False):
        app._pr_poll_started = True
        runner = threading.Thread(target=_run_pr_poll_loop, args=(app,), kwargs={"pr_poll_seconds": 10}, daemon=True)
        runner.start()

    # Health check endpoint
    @app.route("/health")
    def health():
        return jsonify({"status": "healthy"})

    return app


if __name__ == "__main__":
    app = create_app()
    # Disable reloader so debug mode does not spawn a second process/thread set.
    app.run(host="0.0.0.0", port=5010, debug=True, use_reloader=False)
