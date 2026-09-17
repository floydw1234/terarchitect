# Week Board — 2026-09-14

Weekday cloud-agent slots for the week of Mon 2026-09-14 through Fri 2026-09-18.
Each point fits one weekday PR: a functional product slice toward the MVP spine.

**This week is functional work only — no docs-only, theme, screenshot-for-screenshot, or outreach tasks.**

Product spine: Ticket → TicketAttempt → accepted attempt → promotion candidate → ShipRun → one release PR → `shipped_frontier`.

---

1. **Mon 2026-09-14 — CLI ticket winner flow on `shipped_frontier`.** Align `ta ticket choose-winner` / `accept-winner` preflight with backend accept rules: compare attempt `base_hash` against `project.shipped_frontier` (not `accepted_frontier_id`), surface `candidate_eligible` and `shipped_frontier` in accept-winner JSON, and add CLI tests for non-dry-run choose-winner POST plus frontier-divergence cases. Verify: `pytest tests/test_ticket_command.py -k "choose_winner or accept_winner or candidate_eligible" backend/tests/test_attempt_lifecycle.py -q`. Status: `done`.

2. **Tue 2026-09-15 — Ship-run ship idempotency + release-PR e2e anchor.** Harden the final ship boundary: idempotent 200 when re-shipping an already-shipped run; recover stale `shipping` runs via reset-stale or PR-reconcile; add `test_e2e` coverage for release-PR merge → `shipped_frontier` advance (mocked `gh`). Verify: `pytest backend/tests/test_e2e.py backend/tests/test_shiproom_hardening.py backend/tests/test_concurrency.py -k "ship" -q`. Status: `done`.

3. **Wed 2026-09-16 — AgentHub boring path: claim → TicketAttempt → finalize.** Unblock GitHub-free swarm ticket execution (require `shipped_frontier`/AgentHub, not `github_url`, until release PR); fail the job when `swarm_publish` or ticket `/complete` fails; extend AGENTHUB_URL host remap (#25 parity) to coordinator host shipper subprocesses. Verify: `pytest backend/tests/test_agenthub.py backend/tests/test_integration.py coordinator/tests/test_docker_runtime_contract.py tests/test_cli_shipper.py -q`. Status: `done`.

4. **Thu 2026-09-17 — Dependency base dispatch for child jobs.** Wire `mvp_dependency_base_context` into job claim/dispatch so a ticket with one accepted-unshipped parent gets `base_hash` from the parent attempt commit (not stale `shipped_frontier`); block unsupported multi-parent bases at claim time. Verify: `pytest backend/tests/test_agenthub.py backend/tests/test_integration.py -k "dependency or base_selection or accepted_dependency" -q`. Status: `done`.

5. **Fri 2026-09-18 — Second CLI dogfood loop from new frontier.** Close one concrete blocker for repeating the full headless loop on reviewFeed (or similar): next independent ticket after first ship bases on advanced `shipped_frontier`, compose → release PR → ship-run → frontier advance without UI. Verify: `pytest backend/tests/test_e2e.py::test_e2e_ship_happy_path backend/tests/test_integration.py -k "frontier" -q` plus CLI sequence `ta ship happy-path <project_id> --ticket <ticket_id> --sync` on spark checkout (`/home/william/Documents/codingProj/terarchitect`). Status: `todo`.
