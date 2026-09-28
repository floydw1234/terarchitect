# Week Board — 2026-09-28

Weekday cloud-agent slots for the week of Mon 2026-09-28 through Fri 2026-10-02.
Each point fits one weekday PR: a functional product slice toward the MVP spine.

**This week is functional work only — no docs-only, theme, screenshot-for-screenshot, or outreach tasks.**

**One-off exception:** docs consolidation to [`ROADMAP.md`](ROADMAP.md) / plan index (this PR).

Product spine: Ticket → TicketAttempt → accepted attempt → promotion candidate → ShipRun → **ship (`ship_target=agenthub` default)** → `shipped_frontier` advances.

**Operator host:** William’s Ubuntu machine **spark** at `/home/william/Documents/codingProj/terarchitect` (headless). Live proof for board items is **CLI (`ta`) / API only** — no UI, browser automation, or React boot.

## Must-haves / spine reminder

Prior week (2026-09-21→25) closed MH3–MH4 and MH6 in CI/cloud; Wed MH1+MH2 added decomposed CLI dogfood e2e (#36); Fri MH5 landed **#38** (dependency compose through ShipRun).

**Critical gap:** Sep 21–25 slices were verified by automated tests in the cloud agent environment only. **First live pass done** (2026-09-28, Smoke 06, AgentHub ship target on spark) — **needs repeatability** before we treat the loop as closed.

Standing priorities this week (pick unfinished slices):

1. Close one full dogfood loop on a real local project via CLI: choose-winner → accept → create-candidate → ShipRun → ship (AgentHub target) → `shipped_frontier` advances.
2. Candidate/ShipRun remains the only ship path (do not reintroduce wave shipping).
3. Harden accept / choose-winner so winners are integrated and `candidate_eligible` without frontier/base drift (CLI paths included).
4. Deterministic base selection after ship: next jobs base on `shipped_frontier` or one accepted-unshipped dependency.
5. CLI operator completeness for that loop on spark.
6. Local AgentHub + OpenCode as the boring worker path (claim → publish → finalize); GitHub only when `ship_target=github`.

Supersedes week board **2026-09-21** (including Fri MH5 item — carry forward via #38 land + this week’s slices, not as the sole Mon goal).

---

1. **Mon 2026-09-28 — Spark-ready CLI dogfood operator path.** **Done via #41** (AgentHub-default ship, host URLs, worker PATH; #40 closed/superseded) **and #42** (`make ci-python`, `AGENTS.md`, CI concurrency). `ta ship operator-loop` runs the decomposed spine with before/after `shipped_frontier` and `next_commands`; #38 merged for dependency ShipRun boundary. Status: `done`.

2. **Tue 2026-09-29 — Live-loop blockers: accept → candidate_eligible without drift.** Harden accept, choose-winner, and CLI surfaces so an accepted winner is immediately `candidate_eligible` and `ta ship create-candidate` succeeds without frontier/base mismatch (MH3 follow-through for the operator path). Touch `backend/api/services/attempt_service.py`, accept/choose-winner routes, `cli/commands/ticket.py` / `cli/commands/attempt.py`, and staleness helpers shared with list/detail. Verify: `pytest backend/tests/test_attempt_lifecycle.py tests/test_ticket_command.py tests/test_cli_attempt.py -k "candidate_eligible or accept_winner or choose_winner or stale" -q`. Status: `todo`.

3. **Wed 2026-09-30 — Local AgentHub + OpenCode claim → publish → finalize (real worker path).** Make the boring path for producing a validated `TicketAttempt` via local AgentHub/OpenCode (stub-free where feasible): extend `tests/integration/test_swarm_real.py` / `agent/agent_runner` while keeping one PR-sized change; coordinator worker claim → swarm publish → finalize must yield a validated attempt usable in Mon’s CLI spine. Verify: `pytest tests/integration/test_swarm_real.py -m swarm_real -k "claim or finalize or publish" -q backend/tests/test_integration.py -k "worker_job_claim" -q`. Status: `todo`.

4. **Thu 2026-10-01 — Auto-advance mode (direction-aligned slice).** When validation + tests are green under **`ship_target=agenthub`**, run choose-winner → accept-winner → create-candidate → compose → ship without manual CLI babysitting (agent-driven or scheduler hook — minimal first slice). Prove with tests; do not claim full merge-on-green policy. Verify: targeted pytest for new automation path + existing dogfood integration tests. Status: `todo`.

5. **Fri 2026-10-02 — `ta ship revert` + two ships in a row (AgentHub target).** Implement **`ta ship revert`** (move `shipped_frontier` back to previous ShipRun base) and close **two sequential ships** via operator-loop / decomposed CLI on AgentHub target; assert `shipped_frontier` moves twice and attempt statuses stay consistent. Verify: `pytest tests/integration/test_cli_dogfood_loop.py backend/tests/test_e2e.py -k "second_ship or dogfood or revert" -q`. Status: `todo`.
