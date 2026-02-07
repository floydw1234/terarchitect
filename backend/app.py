"""
Terarchitect Backend - Flask Application
"""
import os
from flask import Flask, jsonify
from flask_cors import CORS
from .models.db import db

def create_app():
    app = Flask(__name__)

    # Configure CORS
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost:3000", "http://127.0.0.1:3000"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type"]
        }
    })

    # Load configuration
    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "postgresql://terarchitect:terarchitect@localhost:5432/terarchitect"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        VLLM_URL=os.environ.get("VLLM_URL", "http://localhost:8000"),
        VLLM_PROXY_URL=os.environ.get("VLLM_PROXY_URL", "http://localhost:8080"),
    )

    # Initialize database
    db.init_app(app)

    # Create tables
    with app.app_context():
        db.create_all()

    # Register blueprints
    from .api import api_bp
    app.register_blueprint(api_bp, url_prefix="/api")

    # Health check endpoint
    @app.route("/health")
    def health():
        return jsonify({"status": "healthy"})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
