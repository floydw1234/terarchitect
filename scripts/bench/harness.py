"""Core logic for the Terarchitect vs baseline comparison harness."""

from __future__ import annotations

import csv
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import urllib.request
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
    ta_ticket_id: str | None = None
    frontier_before: str | None = None
    frontier_after: str | None = None

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
            "ta_ticket_id": self.ta_ticket_id or "",
            "frontier_before": (self.frontier_before or "")[:12],
            "frontier_after": (self.frontier_after or "")[:12],
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

    in_match = re.search(r"(?:input|prompt)\s*tokens?[:\s]+(\d+)", text, re.I)
    out_match = re.search(r"(?:output|completion)\s*tokens?[:\s]+(\d+)", text, re.I)
    if in_match:
        tokens_in = int(in_match.group(1))
    if out_match:
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
    # Count new untracked files too (intent-to-add keeps the index otherwise unchanged).
    run_subprocess(["git", "add", "-A", "-N"], cwd=repo_root, runner=runner)
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
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    run = runner or subprocess.run
    return run(
        list(cmd),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_ci_python(
    repo_root: Path,
    *,
    ci_command: Sequence[str] = DEFAULT_CI_COMMAND,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    proc = run_subprocess(ci_command, cwd=repo_root, runner=runner)
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
    attempt_count: int | None = None,
    timeout_sec: int = 3600,
) -> list[str]:
    prefix = _ta_invocation_prefix(ta_bin)
    if use_top_level_run:
        cmd = [
            *prefix,
            "--json",
            "run",
            spec.project_id or "",
            spec.title,
            "--description",
            spec.prompt,
            "--timeout",
            str(int(timeout_sec)),
        ]
        if attempt_count:
            cmd += ["--attempt-count", str(int(attempt_count))]
        return cmd
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
        if not spec.project_id:
            raise HarnessError(
                f"ticket {spec.id}: `ta run` requires a project_id (YAML or --project-id)"
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
        start_rev = run_subprocess(["git", "rev-parse", "HEAD"], cwd=worktree, runner=runner)
        start_sha = (start_rev.stdout or "").strip() or None
        agent_proc = run_subprocess(agent_cmd, cwd=worktree, runner=runner)
        combined = (agent_proc.stdout or "") + (agent_proc.stderr or "")
        tin, tout, cost = parse_token_usage(combined)
        result.tokens_in = tin
        result.tokens_out = tout
        result.cost_usd = cost
        if agent_proc.returncode != 0:
            result.error = f"agent exited {agent_proc.returncode}: {agent_proc.stderr.strip()[:500]}"

        result.ci_pass = run_ci_python(worktree, ci_command=ci_command, runner=runner)
        files, added, removed = git_diff_stats(worktree, base_ref=start_sha, runner=runner)
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


def parse_agent_result(text: str) -> dict[str, Any] | None:
    """Return the last JSON object in ``text`` that looks like an agent-result envelope."""
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    idx = 0
    while True:
        idx = text.find("{", idx)
        if idx < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except ValueError:
            idx += 1
            continue
        if isinstance(obj, dict) and ("status" in obj or "schema_version" in obj):
            found = obj
        idx = end
    return found


def fetch_agenthub_commit(
    repo_root: Path,
    commit: str,
    *,
    agenthub_url: str | None = None,
    api_key: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Fetch an AgentHub commit (bundle) into ``repo_root`` so it can be checked out."""
    url = (
        agenthub_url
        or os.environ.get("TERARCHITECT_AGENTHUB_URL")
        or os.environ.get("AGENTHUB_URL")
        or "http://127.0.0.1:8088"
    ).rstrip("/")
    key = api_key if api_key is not None else os.environ.get("AGENTHUB_API_KEY", "")
    req = urllib.request.Request(f"{url}/api/git/fetch/{commit}")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as handle:
        bundle = handle.name
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                handle.write(resp.read())
        except Exception as exc:  # noqa: BLE001
            raise HarnessError(f"AgentHub fetch {commit[:12]} failed: {exc}") from exc
    try:
        proc = run_subprocess(["git", "fetch", "--quiet", bundle, commit], cwd=repo_root, runner=runner)
        if proc.returncode != 0:
            # Bundles are created from a temporary ref; fetching all heads is the fallback.
            proc = run_subprocess(
                ["git", "fetch", "--quiet", bundle, "+refs/*:refs/bench/*"], cwd=repo_root, runner=runner
            )
        if proc.returncode != 0:
            raise HarnessError(f"git fetch of AgentHub bundle failed: {proc.stderr.strip()[:300]}")
    finally:
        try:
            os.unlink(bundle)
        except OSError:
            pass


def evaluate_shipped_commit(
    result: BenchmarkResult,
    *,
    repo_root: Path,
    ci_command: Sequence[str] = DEFAULT_CI_COMMAND,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    fetcher: Callable[..., None] = fetch_agenthub_commit,
) -> None:
    """Run CI and diff stats on the shipped frontier commit in a scratch worktree."""
    after = result.frontier_after
    if not after:
        return
    fetcher(repo_root, after, runner=runner)
    if result.frontier_before:
        try:
            fetcher(repo_root, result.frontier_before, runner=runner)
        except HarnessError:
            pass
    parent = repo_root / ".bench-worktrees"
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f"ta-{int(time.time() * 1000)}"
    add = run_subprocess(["git", "worktree", "add", "--detach", str(path), after], cwd=repo_root, runner=runner)
    if add.returncode != 0:
        raise HarnessError(f"git worktree add {after[:12]} failed: {add.stderr.strip()[:300]}")
    try:
        result.ci_pass = run_ci_python(path, ci_command=ci_command, runner=runner)
        if result.frontier_before:
            proc = run_subprocess(
                ["git", "diff", "--numstat", result.frontier_before, after], cwd=path, runner=runner
            )
            files = added = removed = 0
            for line in (proc.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) < 3 or parts[0] == "-" or parts[1] == "-":
                    continue
                files += 1
                added += int(parts[0])
                removed += int(parts[1])
            result.files_changed, result.lines_added, result.lines_removed = files, added, removed
    finally:
        try:
            remove_worktree(repo_root, path, runner=runner)
        except HarnessError:
            pass


def run_terarchitect_ticket(
    spec: TicketSpec,
    *,
    repo_root: Path,
    dry_run: bool = False,
    ta_bin: str | None = None,
    ci_command: Sequence[str] = DEFAULT_CI_COMMAND,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    attempt_count: int | None = None,
    timeout_sec: int = 3600,
    evaluate: bool = True,
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
            attempt_count=attempt_count,
            timeout_sec=timeout_sec,
        )
    except HarnessError as exc:
        result.error = str(exc)
        return result

    if dry_run:
        result.error = f"dry-run: would run {shlex.join(cmd)}"
        return result

    started = time.monotonic()
    try:
        proc = run_subprocess(cmd, cwd=repo_root, runner=runner, timeout=timeout_sec + 600)
        combined = (proc.stdout or "") + (proc.stderr or "")
        tin, tout, cost = parse_token_usage(combined)
        result.tokens_in = tin
        result.tokens_out = tout
        result.cost_usd = cost
        envelope = parse_agent_result(proc.stdout or "") or parse_agent_result(combined)
        if envelope is not None:
            result.shipped = envelope.get("status") == "shipped"
            result.ta_ticket_id = envelope.get("ticket_id")
            result.frontier_before = envelope.get("shipped_frontier_before")
            result.frontier_after = envelope.get("shipped_frontier_after")
            if envelope.get("status") != "shipped":
                result.error = f"ta status={envelope.get('status')}: {envelope.get('failure_reason') or ''}".strip()
        else:
            shipped = detect_shipped_from_output(combined)
            result.shipped = shipped if shipped is not None else (proc.returncode == 0 and use_loop)
        if proc.returncode != 0 and not result.error:
            result.error = f"ta exited {proc.returncode}: {combined.strip()[-500:]}"
        result.wall_time_sec = time.monotonic() - started
        if evaluate and result.shipped and result.frontier_after:
            evaluate_shipped_commit(result, repo_root=repo_root, ci_command=ci_command, runner=runner)
        elif use_loop:
            result.ci_pass = run_ci_python(repo_root, ci_command=ci_command, runner=runner)
    except (HarnessError, subprocess.TimeoutExpired) as exc:
        result.error = str(exc)
    finally:
        if result.wall_time_sec is None:
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
    attempt_count: int | None = None,
    timeout_sec: int = 3600,
    project_id: str | None = None,
    only: Sequence[str] | None = None,
) -> list[BenchmarkResult]:
    if mode not in {"terarchitect", "baseline"}:
        raise HarnessError(f"unsupported mode: {mode}")

    results: list[BenchmarkResult] = []
    for spec in specs:
        if only and spec.id not in only:
            continue
        if project_id and not spec.project_id:
            spec = TicketSpec(
                id=spec.id,
                title=spec.title,
                prompt=spec.prompt,
                project_id=project_id,
                ticket_id=spec.ticket_id,
                attempt_id=spec.attempt_id,
                extra=spec.extra,
            )
        if mode == "baseline":
            results.append(
                run_baseline_ticket(
                    spec,
                    repo_root=repo_root,
                    dry_run=dry_run,
                    runner=runner,
                )
            )
        else:
            results.append(
                run_terarchitect_ticket(
                    spec,
                    repo_root=repo_root,
                    dry_run=dry_run,
                    ta_bin=ta_bin,
                    runner=runner,
                    attempt_count=attempt_count,
                    timeout_sec=timeout_sec,
                )
            )
        print(f"[bench] {mode} {spec.id}: {results[-1].to_row()}", flush=True)
    return results
