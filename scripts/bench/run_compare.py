#!/usr/bin/env python3
"""Run Terarchitect vs baseline comparison harness (see README.md)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench.harness import (  # noqa: E402
    HarnessError,
    flush_results_snapshot,
    load_ticket_specs,
    run_harness,
    write_csv,
    write_markdown,
)


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _REPO_ROOT / "bench-results" / stamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare Terarchitect (ta) vs baseline Cursor agent on ticket specs.",
    )
    parser.add_argument(
        "spec",
        type=Path,
        nargs="?",
        default=_REPO_ROOT / "scripts" / "bench" / "tickets.yaml",
        help="YAML file of ticket specs (default: scripts/bench/tickets.yaml)",
    )
    parser.add_argument(
        "--mode",
        choices=["terarchitect", "baseline"],
        required=True,
        help="terarchitect: ta run or operator-loop; baseline: agent in git worktree",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse specs and print planned commands without running agents or CI",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for results.csv and results.md (default: bench-results/<utc-timestamp>)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Terarchitect repo root (default: repository containing this script)",
    )
    parser.add_argument(
        "--ta-bin",
        default=None,
        help="Path to ta executable (default: python -m cli from repo root)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = args.output_dir or _default_output_dir()
    title = f"Bench results ({args.mode})"

    try:
        specs = load_ticket_specs(args.spec)

        def _persist(_row, rows) -> None:
            if args.dry_run:
                return
            flush_results_snapshot(out_dir, rows, title=title)

        results = run_harness(
            specs,
            mode=args.mode,
            repo_root=args.repo_root.resolve(),
            dry_run=args.dry_run,
            ta_bin=args.ta_bin,
            on_result=_persist,
        )
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for row in results:
            print(f"[dry-run] {row.ticket_id}: {row.error or 'ok'}")
    else:
        write_csv(out_dir / "results.csv", results)
        write_markdown(out_dir / "results.md", results, title=title)
        print(f"Wrote {out_dir / 'results.csv'}")
        print(f"Wrote {out_dir / 'results.md'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
