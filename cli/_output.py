"""Output formatting helpers (table and JSON)."""

import json
import sys
from typing import Any

from cli._api import APIError


def die(error: Any, code: int = 1, *, output: str = "human") -> None:
    print_error(error, output=output)
    sys.exit(code)


def print_json(data: Any, *, stream = None) -> None:
    if stream is None:
        stream = sys.stdout
    print(json.dumps(data, indent=2, default=str), file=stream)


def build_error_payload(error: Any) -> dict[str, Any]:
    if isinstance(error, APIError):
        return {"error": error.to_dict()}
    return {"error": {"message": str(error)}}


def print_error(error: Any, *, output: str = "human") -> None:
    if output == "json":
        print_json(build_error_payload(error), stream=sys.stderr)
        return

    if isinstance(error, APIError):
        print(f"Error: {error.message}", file=sys.stderr)
        if error.detail:
            print(f"Detail: {error.detail}", file=sys.stderr)
        if error.hint:
            print(f"Hint: {error.hint}", file=sys.stderr)
        if error.phase:
            print(f"Phase: {error.phase}", file=sys.stderr)
        if error.request_id:
            print(f"Request ID: {error.request_id}", file=sys.stderr)
        if error.next_commands:
            print("Next:", file=sys.stderr)
            for command in error.next_commands:
                print(f"  {command}", file=sys.stderr)
        return

    print(f"Error: {error}", file=sys.stderr)


def print_receipt(
    title: str,
    *,
    fields: list[tuple[str, Any]] | None = None,
    next_commands: list[str] | None = None,
) -> None:
    print(title)
    if fields:
        width = max(len(label) for label, _ in fields)
        for label, value in fields:
            print(f"{label}:".ljust(width + 2), value)
    if next_commands:
        print("")
        print("Next:")
        for command in next_commands:
            print(f"  {command}")


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
