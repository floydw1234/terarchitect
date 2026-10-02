"""Unit tests for scripts/bench comparison harness (no live agent calls)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bench.harness import (  # noqa: E402
    BenchmarkResult,
    HarnessError,
    build_terarchitect_command,
    git_diff_stats,
    load_ticket_specs,
    parse_token_usage,
    render_markdown_table,
    resolve_terarchitect_strategy,
    run_baseline_ticket,
    run_harness,
    run_terarchitect_ticket,
    write_csv,
    write_markdown,
)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tickets.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_load_ticket_specs_parses_terarchitect_block(tmp_path: Path):
    path = _write_yaml(
        tmp_path,
        """
        tickets:
          - id: t1
            title: One
            prompt: Do the thing.
            terarchitect:
              project_id: proj-1
              ticket_id: tick-1
              attempt_id: att-1
        """,
    )
    specs = load_ticket_specs(path)
    assert len(specs) == 1
    assert specs[0].id == "t1"
    assert specs[0].project_id == "proj-1"
    assert specs[0].ticket_id == "tick-1"
    assert specs[0].attempt_id == "att-1"


def test_load_ticket_specs_requires_fields(tmp_path: Path):
    path = _write_yaml(
        tmp_path,
        """
        tickets:
          - id: only-id
            title: Missing prompt
        """,
    )
    with pytest.raises(HarnessError, match="prompt"):
        load_ticket_specs(path)


def test_benchmark_result_row_and_csv_roundtrip(tmp_path: Path):
    results = [
        BenchmarkResult(
            ticket_id="a",
            mode="baseline",
            wall_time_sec=1.5,
            ci_pass=True,
            shipped=False,
            files_changed=2,
            lines_added=10,
            lines_removed=3,
        )
    ]
    table = render_markdown_table(results)
    assert "ticket_id" in table
    assert "| a |" in table

    out = tmp_path / "out" / "results.csv"
    write_csv(out, results)
    assert "ci_pass" in out.read_text(encoding="utf-8")

    md = tmp_path / "results.md"
    write_markdown(md, results, title="Test run")
    assert "# Test run" in md.read_text(encoding="utf-8")


def test_parse_token_usage():
    text = "Usage: input tokens: 1200, output tokens: 340, cost $0.42"
    tin, tout, cost = parse_token_usage(text)
    assert tin == 1200
    assert tout == 340
    assert cost == "0.42"


def test_git_diff_stats_parses_numstat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, cwd=None, capture_output=True, text=True, check=False):
        assert cmd[:3] == ["git", "diff", "--numstat"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="3\t1\tfoo.py\n10\t0\tbar.py\n",
            stderr="",
        )

    monkeypatch.setattr("scripts.bench.harness.run_subprocess", lambda cmd, **kw: fake_run(cmd, cwd=kw.get("cwd")))
    files, added, removed = git_diff_stats(tmp_path)
    assert files == 2
    assert added == 13
    assert removed == 1


def test_resolve_terarchitect_strategy_operator_loop(tmp_path: Path):
    spec = load_ticket_specs(
        _write_yaml(
            tmp_path,
            """
            tickets:
              - id: x
                title: T
                prompt: P
                terarchitect:
                  project_id: p
                  ticket_id: t
                  attempt_id: a
            """,
        )
    )[0]

    def runner(cmd, **kwargs):
        # ta run --help fails; operator-loop path
        if "run" in cmd and "--help" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    use_run, use_loop = resolve_terarchitect_strategy(spec, runner=runner)
    assert use_run is False
    assert use_loop is True
    cmd = build_terarchitect_command(
        spec, use_top_level_run=False, use_operator_loop=True
    )
    assert cmd[-3:] == ["p", "t", "a"]


def test_run_terarchitect_ticket_dry_run(tmp_path: Path):
    spec = load_ticket_specs(
        _write_yaml(
            tmp_path,
            """
            tickets:
              - id: dry
                title: Dry
                prompt: noop
                terarchitect:
                  project_id: p
                  ticket_id: t
                  attempt_id: a
            """,
        )
    )[0]

    def runner(cmd, **kwargs):
        if len(cmd) >= 2 and cmd[-2] == "run" and cmd[-1] == "--help":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = run_terarchitect_ticket(
        spec, repo_root=tmp_path, dry_run=True, runner=runner
    )
    assert result.dry_run is True
    assert "operator-loop" in (result.error or "")


def test_run_baseline_ticket_mocked_subprocess(tmp_path: Path):
    spec = load_ticket_specs(
        _write_yaml(
            tmp_path,
            """
            tickets:
              - id: base1
                title: Base
                prompt: Fix docs
            """,
        )
    )[0]

    calls: list[list[str]] = []

    def runner(cmd, cwd=None, capture_output=True, text=True, timeout=None, check=False):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "worktree", "add"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "agent":
            return subprocess.CompletedProcess(
                cmd, 0, "output tokens: 50", "", 
            )
        if cmd[0] == "make":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "diff", "--numstat"]:
            return subprocess.CompletedProcess(cmd, 0, "1\t2\tfile.py\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = run_baseline_ticket(spec, repo_root=tmp_path, runner=runner)
    assert result.ci_pass is True
    assert result.tokens_out == 50
    assert result.files_changed == 1
    assert result.lines_added == 1
    assert result.lines_removed == 2
    assert any(c[:3] == ["git", "worktree", "remove"] for c in calls)


def test_run_harness_terarchitect_unsupported(tmp_path: Path):
    specs = load_ticket_specs(
        _write_yaml(
            tmp_path,
            """
            tickets:
              - id: no-ids
                title: T
                prompt: P
            """,
        )
    )

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "unknown")

    results = run_harness(
        specs, mode="terarchitect", repo_root=tmp_path, runner=runner
    )
    assert len(results) == 1
    assert "not yet supported" in (results[0].error or "").lower()


def test_run_compare_main_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spec_path = _write_yaml(
        tmp_path,
        """
        tickets:
          - id: one
            title: T
            prompt: P
        """,
    )
    from scripts.bench import run_compare

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_compare.py",
            str(spec_path),
            "--mode",
            "baseline",
            "--dry-run",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert run_compare.main() == 0
