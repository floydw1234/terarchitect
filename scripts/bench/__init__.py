"""Comparison harness for Terarchitect vs baseline agent runs (see README.md)."""

from .harness import (
    BenchmarkResult,
    TicketSpec,
    load_ticket_specs,
    render_markdown_table,
    results_to_csv_rows,
    write_csv,
    write_markdown,
)

__all__ = [
    "BenchmarkResult",
    "TicketSpec",
    "load_ticket_specs",
    "render_markdown_table",
    "results_to_csv_rows",
    "write_csv",
    "write_markdown",
]
