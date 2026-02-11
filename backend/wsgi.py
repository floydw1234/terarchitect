"""
WSGI entry point for gunicorn
"""
import os
import sys

# Add the backend directory to the path (for local run and Docker)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the models first to ensure modules are registered
from models.db import db

# Now import main
import main

app = main.create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=True)
