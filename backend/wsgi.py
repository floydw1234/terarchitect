"""
WSGI entry point for gunicorn
"""
import os
import sys

# Add the app directory to the path
sys.path.insert(0, '/app')

# Import the models first to ensure modules are registered
from models.db import db

# Now import main
import main

app = main.create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
