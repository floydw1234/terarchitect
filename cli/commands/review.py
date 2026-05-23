"""review subcommand — DEPRECATED.

Per-ticket PR review has been removed. Use 'ta ship' for Ship Room operations.

This command is kept as a stub so existing scripts fail gracefully instead of
silently producing unexpected behavior.
"""

from cli._output import die


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "review",
        help="[DEPRECATED] Per-ticket PR review removed. Use 'ta ship' instead.",
    )
    p.set_defaults(func=_deprecated)


def _deprecated(args, api) -> None:
    die(
        "'ta review' has been removed — per-ticket PR review is no longer supported.\n"
        "Use 'ta ship waves <project_id>' to see wave status and release PRs."
    )
