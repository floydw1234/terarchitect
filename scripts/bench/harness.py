"""Core logic for the Terarchitect vs baseline comparison harness."""

from __future__ import annotations

import csv
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

DEFAULT_CI_COMMAND = ("make", "ci-python")
DEFAULT_BASELINE_AGENT = (
    "agent",
    "-p",
    "--force",
    "--model",
    "composer-2.5",
    "--output-format",
    "text",
)


class HarnessError(Exception):
    """User-facing harness failure."""


@dataclass(frozen=True)
class TicketSpec:
    id: str
    title: str
    prompt: str
    project_id: str | None = None
    ticket_id: str | None = None
    attempt_id: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    ticket_id: str
    mode: str
    wall_time_sec: float | None = None
    cost_usd: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    ci_pass: bool | None = None
    shipped: bool | None = None
    files_changed: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    error: str | None = None
    dry_run: bool = False

    def to_row(self) -> dict[str, str]:
        def fmt_bool(value: bool | None) -> str:
            if value is None:
                return ""
            return "yes" if value else "no"

        def fmt_num(value: float | int | None) -> str:
            if value is None:
                return ""
            if isinstance(value, float):
                return f"{value:.3f}"
            return str(value)

        return {
            "ticket_id": self.ticket_id,
            "mode": self.mode,
            "wall_time_sec": fmt_num(self.wall_time_sec),
            "cost_usd": self.cost_usd or "",
            "tokens_in": fmt_num(self.tokens_in),
            "tokens_out": fmt_num(self.tokens_out),
            "ci_pass": fmt_bool(self.ci_pass),
            "shipped": fmt_bool(self.shipped),
            "files_changed": fmt_num(self.files_changed),
            "lines_added": fmt_num(self.lines_added),
            "lines_removed": fmt_num(self.lines_removed),
            "error": self.error or "",
            "dry_run": fmt_bool(self.dry_run),
        }


CSV_FIELDNAMES: tuple[str, ...] = tuple(BenchmarkResult(ticket_id="", mode="").to_row().keys())


def load_ticket_specs(path: Path | str) -> list[TicketSpec]:
    """Load ticket definitions from YAML."""
    raw_path = Path(path)
    data = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise HarnessError(f"{raw_path}: expected mapping at top level")
    tickets = data.get("tickets")
    if not isinstance(tickets, list) or not tickets:
        raise HarnessError(f"{raw_path}: expected non-empty 'tickets' list")

    specs: list[TicketSpec] = []
    for index, item in enumerate(tickets):
        if not isinstance(item, dict):
            raise HarnessError(f"{raw_path}: tickets[{index}] must be a mapping")
        ticket_id = item.get("id")
        title = item.get("title")
        prompt = item.get("prompt")
        if not ticket_id or not title or not prompt:
            raise HarnessError(
                f"{raw_path}: tickets[{index}] requires id, title, and prompt"
            )
        terarch = item.get("terarchitect") or {}
        if terarch and not isinstance(terarch, dict):
            raise HarnessError(f"{raw_path}: tickets[{index}].terarchitect must be a mapping")
        specs.append(
            TicketSpec(
                id=str(ticket_id),
                title=str(title),
                prompt=str(prompt).strip(),
                project_id=_optional_str(terarch.get("project_id") or item.get("project_id")),
                ticket_id=_optional_str(terarch.get("ticket_id") or item.get("ticket_id")),
                attempt_id=_optional_str(terarch.get("attempt_id") or item.get("attempt_id")),
                extra={k: v for k, v in item.items() if k not in {"id", "title", "prompt", "terarchitect"}},
            )
        )
    return specs


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def results_to_csv_rows(results: Sequence[BenchmarkResult]) -> list[dict[str, str]]:
    return [result.to_row() for result in results]


def write_csv(path: Path | str, results: Sequence[BenchmarkResult]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = results_to_csv_rows(results)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)


def append_results_csv(path: Path | str, results: Sequence[BenchmarkResult]) -> None:
    """Rewrite CSV with all rows accumulated so far (safe for interrupted runs)."""
    write_csv(path, results)


def flush_results_snapshot(
    path: Path | str,
    results: Sequence[BenchmarkResult],
    *,
    title: str,
) -> None:
    """Persist partial bench output (CSV + markdown) after each ticket."""
    out_dir = Path(path)
    append_results_csv(out_dir / "results.csv", results)
    write_markdown(out_dir / "results.md", results, title=title)


def render_markdown_table(results: Sequence[BenchmarkResult]) -> str:
    if not results:
        return "| (no results) |\n| --- |\n"

    rows = results_to_csv_rows(results)
    headers = list(CSV_FIELDNAMES)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = [row[h].replace("|", "\\|") for h in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_markdown(path: Path | str, results: Sequence[BenchmarkResult], *, title: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n\n{render_markdown_table(results)}"
    out.write_text(body, encoding="utf-8")


def parse_token_usage(text: str) -> tuple[int | None, int | None, str | None]:
    """Best-effort parse of token/cost hints from agent CLI output."""
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost: str | None = None

    json_in = re.search(r'"input_tokens"\s*:\s*(\d+)', text)
    json_out = re.search(r'"output_tokens"\s*:\s*(\d+)', text)
    if json_in:
        tokens_in = int(json_in.group(1))
    if json_out:
        tokens_out = int(json_out.group(1))

    in_match = re.search(r"(?:input|prompt)\s*tokens?[:\s]+(\d+)", text, re.I)
    out_match = re.search(r"(?:output|completion)\s*tokens?[:\s]+(\d+)", text, re.I)
    if in_match and tokens_in is None:
        tokens_in = int(in_match.group(1))
    if out_match and tokens_out is None:
        tokens_out = int(out_match.group(1))

    cost_match = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)", text)
    if cost_match:
        cost = cost_match.group(1)

    return tokens_in, tokens_out, cost


def git_diff_stats(
    repo_root: Path,
    base_ref: str | None = "origin/main",
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[int, int, int]:
    """Return (files_changed, lines_added, lines_removed) for working tree vs base_ref.

    When base_ref is None, diff the working tree and index against HEAD (scratch worktrees).
    """
    cmd = ["git", "diff", "--numstat"]
    if base_ref is None:
        cmd.append("HEAD")
    else:
        cmd.append(base_ref)
    proc = run_subprocess(cmd, cwd=repo_root, runner=runner)
    if proc.returncode != 0:
        raise HarnessError(
            f"git diff --numstat {base_ref} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    files = 0
    added = 0
    removed = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, r, _path = parts[0], parts[1], parts[2]
        if a == "-" or r == "-":
            continue
        files += 1
        added += int(a)
        removed += int(r)
    return files, added, removed


def run_subprocess(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    run = runner or subprocess.run
    kwargs: dict = {
        "cwd": cwd,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if env is not None:
        kwargs["env"] = env
    return run(list(cmd), **kwargs)


def ci_subprocess_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        env["PYTEST_PYTHON"] = str(venv_python)
    return env


def run_ci_python(
    repo_root: Path,
    *,
    ci_command: Sequence[str] = DEFAULT_CI_COMMAND,
    cwd: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    target = cwd or repo_root
    env = ci_subprocess_env(repo_root)
    if ci_command == DEFAULT_CI_COMMAND:
        script = repo_root / "scripts" / "ci-python.sh"
        proc = run_subprocess(["bash", str(script)], cwd=target, env=env, runner=runner)
    else:
        proc = run_subprocess(ci_command, cwd=target, env=env, runner=runner)
    return proc.returncode == 0


def _ta_invocation_prefix(ta_bin: str | None) -> list[str]:
    if ta_bin:
        return [ta_bin]
    return ["python", "-m", "cli"]


def ta_top_level_run_available(
    *,
    ta_bin: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    prefix = _ta_invocation_prefix(ta_bin)
    proc = run_subprocess([*prefix, "run", "--help"], runner=runner)
    return proc.returncode == 0


def build_terarchitect_command(
    spec: TicketSpec,
    *,
    ta_bin: str | None = None,
    use_top_level_run: bool,
    use_operator_loop: bool,
) -> list[str]:
    prefix = _ta_invocation_prefix(ta_bin)
    if use_top_level_run:
        return [*prefix, "run", spec.project_id or "", spec.ticket_id or ""]
    if use_operator_loop:
        if not (spec.project_id and spec.ticket_id and spec.attempt_id):
            raise HarnessError(
                "terarchitect operator-loop mode requires project_id, ticket_id, and attempt_id "
                f"on ticket {spec.id}"
            )
        return [
            *prefix,
            "ship",
            "operator-loop",
            spec.project_id,
            spec.ticket_id,
            spec.attempt_id,
        ]
    raise HarnessError(
        "terarchitect mode is not yet supported: no `ta run` command and operator-loop "
        f"fields missing for ticket {spec.id}"
    )


def resolve_terarchitect_strategy(
    spec: TicketSpec,
    *,
    ta_bin: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[bool, bool]:
    """Return (use_top_level_run, use_operator_loop)."""
    if ta_top_level_run_available(ta_bin=ta_bin, runner=runner):
        if not (spec.project_id and spec.ticket_id):
            raise HarnessError(
                f"ticket {spec.id}: `ta run` requires project_id and ticket_id in YAML"
            )
        return True, False
    if spec.project_id and spec.ticket_id and spec.attempt_id:
        return False, True
    raise HarnessError(
        "terarchitect mode is not yet supported for this ticket: "
        "`ta run` is unavailable and operator-loop requires project_id, ticket_id, and attempt_id. "
        "Use baseline mode or extend the harness when `ta run` lands."
    )


def detect_shipped_from_output(text: str) -> bool | None:
    lowered = text.lower()
    if "shipped_frontier" in lowered and "after" in lowered:
        if re.search(r"shipped_frontier.*after", lowered):
            return True
    if '"shipped": true' in lowered or "'shipped': true" in lowered:
        return True
    if "ship_run" in lowered and "shipped" in lowered:
        return True
    return None


def create_scratch_worktree(
    repo_root: Path,
    *,
    base_ref: str = "origin/main",
    worktree_parent: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> Path:
    parent = worktree_parent or (repo_root / ".bench-worktrees")
    parent.mkdir(parents=True, exist_ok=True)
    name = f"bench-{int(time.time() * 1000)}"
    path = parent / name
    fetch = run_subprocess(["git", "fetch", "origin", "main"], cwd=repo_root, runner=runner)
    if fetch.returncode != 0:
        raise HarnessError(f"git fetch failed: {fetch.stderr.strip()}")
    add = run_subprocess(
        ["git", "worktree", "add", str(path), base_ref],
        cwd=repo_root,
        runner=runner,
    )
    if add.returncode != 0:
        raise HarnessError(f"git worktree add failed: {add.stderr.strip()}")
    return path


def remove_worktree(
    repo_root: Path,
    worktree_path: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    proc = run_subprocess(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_root,
        runner=runner,
    )
    if proc.returncode != 0:
        raise HarnessError(f"git worktree remove failed: {proc.stderr.strip()}")


def run_baseline_ticket(
    spec: TicketSpec,
    *,
    repo_root: Path,
    dry_run: bool = False,
    base_ref: str = "origin/main",
    agent_cmd_prefix: Sequence[str] = DEFAULT_BASELINE_AGENT,
    ci_command: Sequence[str] = DEFAULT_CI_COMMAND,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> BenchmarkResult:
    result = BenchmarkResult(ticket_id=spec.id, mode="baseline", dry_run=dry_run)
    agent_cmd = [*agent_cmd_prefix, spec.prompt]
    if dry_run:
        result.error = "dry-run"
        return result

    worktree: Path | None = None
    started = time.monotonic()
    try:
        worktree = create_scratch_worktree(
            repo_root, base_ref=base_ref, runner=runner
        )
        agent_proc = run_subprocess(agent_cmd, cwd=worktree, runner=runner)
        combined = (agent_proc.stdout or "") + (agent_proc.stderr or "")
        tin, tout, cost = parse_token_usage(combined)
        result.tokens_in = tin
        result.tokens_out = tout
        result.cost_usd = cost
        if agent_proc.returncode != 0:
            result.error = f"agent exited {agent_proc.returncode}: {agent_proc.stderr.strip()[:500]}"

        result.ci_pass = run_ci_python(worktree, ci_command=ci_command, cwd=worktree, runner=runner)
        files, added, removed = git_diff_stats(worktree, base_ref=None, runner=runner)
        result.files_changed = files
        result.lines_added = added
        result.lines_removed = removed
        result.shipped = False
    except HarnessError as exc:
        result.error = str(exc)
    finally:
        result.wall_time_sec = time.monotonic() - started
        if worktree is not None:
            try:
                remove_worktree(repo_root, worktree, runner=runner)
            except HarnessError as exc:
                suffix = str(exc)
                result.error = f"{result.error}; {suffix}" if result.error else suffix
    return result


def run_terarchitect_ticket(
    spec: TicketSpec,
    *,
    repo_root: Path,
    dry_run: bool = False,
    ta_bin: str | None = None,
    ci_command: Sequence[str] = DEFAULT_CI_COMMAND,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> BenchmarkResult:
    result = BenchmarkResult(ticket_id=spec.id, mode="terarchitect", dry_run=dry_run)
    try:
        use_run, use_loop = resolve_terarchitect_strategy(
            spec, ta_bin=ta_bin, runner=runner
        )
        cmd = build_terarchitect_command(
            spec,
            ta_bin=ta_bin,
            use_top_level_run=use_run,
            use_operator_loop=use_loop,
        )
    except HarnessError as exc:
        result.error = str(exc)
        return result

    if dry_run:
        result.error = f"dry-run: would run {shlex.join(cmd)}"
        return result

    started = time.monotonic()
    try:
        proc = run_subprocess(cmd, cwd=repo_root, runner=runner)
        combined = (proc.stdout or "") + (proc.stderr or "")
        tin, tout, cost = parse_token_usage(combined)
        result.tokens_in = tin
        result.tokens_out = tout
        result.cost_usd = cost
        if proc.returncode != 0:
            result.error = f"ta exited {proc.returncode}: {proc.stderr.strip()[:500]}"
        shipped = detect_shipped_from_output(combined)
        result.shipped = shipped if shipped is not None else (proc.returncode == 0 and use_loop)
        result.ci_pass = run_ci_python(repo_root, ci_command=ci_command, runner=runner)
        files, added, removed = git_diff_stats(repo_root, runner=runner)
        result.files_changed = files
        result.lines_added = added
        result.lines_removed = removed
    except HarnessError as exc:
        result.error = str(exc)
    finally:
        result.wall_time_sec = time.monotonic() - started
    return result


def run_harness(
    specs: Iterable[TicketSpec],
    *,
    mode: str,
    repo_root: Path,
    dry_run: bool = False,
    ta_bin: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    on_result: Callable[[BenchmarkResult, list[BenchmarkResult]], None] | None = None,
) -> list[BenchmarkResult]:
    if mode not in {"terarchitect", "baseline"}:
        raise HarnessError(f"unsupported mode: {mode}")

    results: list[BenchmarkResult] = []
    for spec in specs:
        if mode == "baseline":
            row = run_baseline_ticket(
                spec,
                repo_root=repo_root,
                dry_run=dry_run,
                runner=runner,
            )
        else:
            row = run_terarchitect_ticket(
                spec,
                repo_root=repo_root,
                dry_run=dry_run,
                ta_bin=ta_bin,
                runner=runner,
            )
        results.append(row)
        if on_result is not None:
            on_result(row, results)
    return results
