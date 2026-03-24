"""Config file loading and environment helpers."""

import json
import os
import sys
from typing import Any


def get_api_url() -> str:
    return (os.environ.get("TERARCHITECT_API_URL") or "http://localhost:5010").rstrip("/")


def load_config_file(path: str) -> Any:
    """Load a JSON or YAML config file. YAML requires pyyaml."""
    if not os.path.isfile(path):
        print(f"Config file not found: {path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(path)[1].lower()
    with open(path, "r") as f:
        content = f.read()

    if ext in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
            return yaml.safe_load(content)
        except ImportError:
            print("pyyaml is required for YAML config files: pip install pyyaml", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON in {path}: {e}", file=sys.stderr)
            sys.exit(1)
