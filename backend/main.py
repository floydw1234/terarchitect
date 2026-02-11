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

    # Configure CORS - allow localhost on any port for development
    CORS(app, resources={
        r"/api/*": {
            "origins": [
                re.compile(r"http://localhost:\d+"),
                re.compile(r"http://127\.0\.0\.1:\d+"),
            ],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type"]
        }
    })

    # Load configuration - DATABASE_URL for local run, SQLALCHEMY_DATABASE_URI overrides
    db_uri = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get(
        "DATABASE_URL",
        "postgresql://terarchitect:terarchitect@localhost:5432/terarchitect",
    )
    app.config.update(
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        VLLM_URL="http://host.docker.internal:8000",
        VLLM_PROXY_URL="http://host.docker.internal:8080",
    )

    # Initialize database
    db.init_app(app)

    # Create tables
    with app.app_context():
        db.create_all()

    # Register blueprints
    from api import api_bp
    app.register_blueprint(api_bp, url_prefix="/api")

    # Health check endpoint
    @app.route("/health")
    def health():
        return jsonify({"status": "healthy"})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5010, debug=True)
