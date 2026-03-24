"""Output formatting helpers (table and JSON)."""

import json
import sys
from typing import Any


def die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def print_table(rows: list, columns: list) -> None:
    """Print a human-readable table.

    columns: list of (key, header) tuples.
    """
    if not rows:
        print("(none)")
        return

    CAP = 48  # max column width
    widths = {key: len(header) for key, header in columns}
    for row in rows:
        for key, _ in columns:
            val = str(row.get(key) or "")
            widths[key] = min(max(widths[key], len(val)), CAP)

    header = "  ".join(h.ljust(widths[k]) for k, h in columns)
    sep = "  ".join("-" * widths[k] for k, _ in columns)
    print(header)
    print(sep)
    for row in rows:
        line = "  ".join(
            str(row.get(k) or "")[:widths[k]].ljust(widths[k]) for k, _ in columns
        )
        print(line)


def short_id(id_str: str, length: int = 8) -> str:
    return (id_str or "")[:length]
