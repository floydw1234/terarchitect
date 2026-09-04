# Week Board — 2026-08-31

Weekday cloud-agent slots for the week of Mon 2026-08-31 through Fri 2026-09-04.
Each point fits one weekday PR: test, bugfix, docs, or screenshot.

---

1. **Mon 2026-08-31 — leftover.** Bugfix: `/api/ready` / `check_execution_readiness` currently requires a GitHub token (`backend/tests/test_memory_backend.py` asserts failure without one). Local AgentHub / worktree mode should be ready without GitHub. Keep GitHub required only for GitHub-backed import/export. Update the existing readiness tests in the same PR. Status: `done`.

2. **Tue 2026-09-01 — docs slice.** Add a short operator note to `docs/RUNBOOK.md` for local-worktree + AgentHub at 127.0.0.1:8088, OpenRouter free/cheap models, Codex off. No secrets. Status: `todo`.

3. **Wed 2026-09-02 — test slice.** Add or extend a focused unit test that `/api/ready` reports ready in local/agenthub mode with only LLM env set (no GitHub token, no embeddings). Depends on point 1 landing; if point 1 already covered this test, skip and take point 4 early. Status: `skipped: covered by point 1 readiness tests (TestExecutionReadinessWithoutEmbeddings in backend/tests/test_memory_backend.py)`.

4. **Thu 2026-09-03 — screenshot.** Refresh `pictures/project_view.png` (or kanban) so README matches current UI after optional-memory. Capture a screenshot ONLY if the app actually boots in the cloud agent. If Docker-in-Docker or the UI will not run, skip the screenshot and note the blocker under this point. Do not fake an image. Status: `blocked: no Docker/Compose in cloud agent environment — docker command not found, cannot start postgres/backend/frontend/agenthub stack to capture real screenshot`.

5. **Fri 2026-09-04 — docs slice.** Add a short pointer in `plans/README.md` (Operator files) to `plans/week-board.md` and `plans/outreach.md`. Do not rewrite the MVP reading order. Status: `done: Operator Files section already present in plans/README.md (added with week-board creation in PR #8)`.
