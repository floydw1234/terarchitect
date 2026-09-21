# Week Board — 2026-09-21

Weekday cloud-agent slots for the week of Mon 2026-09-21 through Fri 2026-09-25.
Each point fits one weekday PR: a functional product slice toward the MVP spine.

**This week is functional work only — no docs-only, theme, screenshot-for-screenshot, or outreach tasks.**

Product spine: Ticket → TicketAttempt → accepted attempt → promotion candidate → ShipRun → one release PR → `shipped_frontier`.

---

1. **Mon 2026-09-21 — Staleness + choose-winner parity on `shipped_frontier`.** Align attempt list/detail `stale` with accept rules: use `compare_base_to_shipped_frontier` (and accepted-unshipped dependency bases) in `attempt_stale_status` / `attempt_to_json` instead of `accepted_frontier_id`; add choose-winner preflight (backend + CLI) that rejects attempts whose `base_hash` would fail accept. Update tests that currently encode accepted-frontier staleness (`test_attempt_list_reports_stale_status_against_accepted_frontier`, `test_choose_winner_does_not_advance_frontiers`) and add reject-on-stale choose coverage. Verify: `pytest backend/tests/test_attempt_lifecycle.py backend/tests/test_agenthub.py backend/tests/test_unit.py tests/test_ticket_command.py -k "stale or choose_winner or accept" -q`. Status: `todo`.

2. **Tue 2026-09-22 — CLI create promotion candidate from accepted attempt.** Add `ta ship create-candidate <project_id> --attempt <attempt_id> [--ticket <ticket_id>]` wrapping `POST /api/projects/{id}/ship/candidates`; extend `accept-winner` and `attempt show` `next_commands` to surface `dry-compose` / `compose-candidate` when `candidate_eligible` (not just `ta ship candidates` list). Verify: `pytest tests/test_cli_ship.py tests/test_ticket_command.py tests/test_cli_attempt.py -k "create_candidate or candidate_eligible or accept_winner" -q`. Status: `todo`.

3. **Wed 2026-09-23 — Decomposed CLI dogfood loop e2e (no happy-path shortcut).** Add one headless integration test driving the explicit operator sequence: `ta ticket evaluate-attempts` → `choose-winner` → `accept-winner` → `ta ship create-candidate` → `compose-candidate --sync` → `ship-run` (mock `gh` like `test_e2e_ship_release_pr_merge_advances_frontier`); assert `shipped_frontier` advance and attempt statuses. Verify: `pytest tests/integration/test_cli_dogfood_loop.py backend/tests/test_e2e.py -k "dogfood or ship_happy_path or release_pr" -q`. Status: `todo`.

4. **Thu 2026-09-24 — Coordinator claim → AgentHub publish → finalize integration tier.** Extend swarm integration so a worker job claim runs `agent.agent_runner` (stub worker first) against real AgentHub: enqueue ticket, `POST /worker/jobs/start`, assert validated `TicketAttempt`, swarm_publish receipt, and ticket `/complete` success — reusing #29 finalize hardening (`test_finalize_raises_when_swarm_publish_fails`). Verify: `pytest tests/integration/test_swarm_real.py -m swarm_real -k "claim or finalize or publish" -q backend/tests/test_integration.py -k "worker_job_claim" -q`. Status: `todo`.

5. **Fri 2026-09-25 — Child-ticket dependency ship loop on parent attempt base.** Close the one-parent-unshipped path end-to-end: parent accept → child dispatch/claim with `mvp_dependency_base_context` base from parent attempt → child validated attempt → compose auto-includes parent → ShipRun → ship → `shipped_frontier` advance (extend `test_compose_auto_includes_unshipped_dependency` / `test_single_dependency_ticket_dispatches_from_parent_attempt_base` with ship boundary, not seeded-only attempts). Verify: `pytest backend/tests/test_integration.py backend/tests/test_agenthub.py backend/tests/test_e2e.py -k "dependency or compose_auto or parent_attempt_base" -q`. Status: `todo`.
