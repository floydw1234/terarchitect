# Week Board — 2026-09-28

Weekday cloud-agent slots for the week of Mon 2026-09-28 through Fri 2026-10-02.
Each point fits one weekday PR: a functional product slice toward the MVP spine.

**This week is functional work only — no docs-only, theme, screenshot-for-screenshot, or outreach tasks.**

Product spine: Ticket → TicketAttempt → accepted attempt → promotion candidate → ShipRun → one release PR → `shipped_frontier`.

**Operator host:** William’s Ubuntu machine **spark** at `/home/william/Documents/codingProj/terarchitect` (headless). Live proof for board items is **CLI (`ta`) / API only** — no UI, browser automation, or React boot.

## Must-haves / spine reminder

Prior week (2026-09-21→25) closed MH3–MH4 and MH6 in CI/cloud; Wed MH1+MH2 added decomposed CLI dogfood e2e (#36); Fri MH5 is open as **PR #38** (dependency compose through ShipRun — CI green, undrafted 2026-09-28, **not merged** at planning kickoff).

**Critical gap:** Sep 21–25 slices were verified by automated tests in the cloud agent environment only. No real ticket has completed the full worker→accept→candidate→ShipRun→release PR→`shipped_frontier` loop **live on spark**.

Standing priorities this week (pick unfinished slices):

1. Close one full dogfood loop on a real local project via CLI: choose-winner → accept → create-candidate → ShipRun → release PR → `shipped_frontier` advances.
2. Candidate/ShipRun remains the only ship path (do not reintroduce wave shipping).
3. Harden accept / choose-winner so winners are integrated and `candidate_eligible` without frontier/base drift (CLI paths included).
4. Deterministic base selection after ship: next jobs base on `shipped_frontier` or one accepted-unshipped dependency.
5. CLI operator completeness for that loop on spark.
6. Local AgentHub + OpenCode as the boring worker path (claim → publish → finalize); GitHub only at release PR.

Supersedes week board **2026-09-21** (including Fri MH5 item — carry forward via #38 land + this week’s slices, not as the sole Mon goal).

---

1. **Mon 2026-09-28 — Spark-ready CLI dogfood operator path.** **Precondition:** merge **#38** if still open (dependency ShipRun boundary on parent attempt base); if already merged, note and proceed. Add or harden a single headless `ta` operator sequence (explicit subcommands only — no API `happy-path` shortcut) that drives evaluate-attempts → choose-winner → accept-winner → create-candidate → compose-candidate --sync → ship-run for one project and prints clear before/after `shipped_frontier` plus `next_commands` (extend `cli/commands/ticket.py`, `cli/commands/ship.py`, `cli/_output.py` / attempt summaries as needed). Target spark CLI use; CI mirrors with mocks like `tests/integration/test_cli_dogfood_loop.py`. Fix any CLI gaps that block that sequence end-to-end. Verify: `pytest tests/integration/test_cli_dogfood_loop.py tests/test_cli_ship.py tests/test_ticket_command.py -q`. Status: `todo` (#38 still open at planning kickoff).

2. **Tue 2026-09-29 — Live-loop blockers: accept → candidate_eligible without drift.** Harden accept, choose-winner, and CLI surfaces so an accepted winner is immediately `candidate_eligible` and `ta ship create-candidate` succeeds without frontier/base mismatch (MH3 follow-through for the operator path). Touch `backend/api/services/attempt_service.py`, accept/choose-winner routes, `cli/commands/ticket.py` / `cli/commands/attempt.py`, and staleness helpers shared with list/detail. Verify: `pytest backend/tests/test_attempt_lifecycle.py tests/test_ticket_command.py tests/test_cli_attempt.py -k "candidate_eligible or accept_winner or choose_winner or stale" -q`. Status: `todo`.

3. **Wed 2026-09-30 — Local AgentHub + OpenCode claim → publish → finalize (real worker path).** Make the boring path for producing a validated `TicketAttempt` via local AgentHub/OpenCode (not GitHub until release PR): extend `tests/integration/test_swarm_real.py` / `agent/agent_runner` beyond stub-only where needed while keeping one PR-sized change; coordinator worker claim → swarm publish → finalize must yield a validated attempt usable in Mon’s CLI spine. Verify: `pytest tests/integration/test_swarm_real.py -m swarm_real -k "claim or finalize or publish" -q backend/tests/test_integration.py -k "worker_job_claim" -q`. Status: `todo`.

4. **Thu 2026-10-01 — After-ship base selection for the next ticket.** After a successful ship, the next **independent** job bases on advanced `shipped_frontier`; a one-parent **child** still dispatches from accepted-unshipped parent attempt base until parent ships. Prove with e2e covering dependency-then-independent sequencing (`backend/tests/test_integration.py` dispatch/base helpers, `backend/tests/test_e2e.py` second-loop behavior — prefer decomposed ship path over legacy happy-path API where tests are touched). Verify: `pytest backend/tests/test_integration.py backend/tests/test_e2e.py -k "shipped_dependency or parent_attempt_base or second_ship or base_leaf" -q`. Status: `todo`.

5. **Fri 2026-10-02 — Second ship from new frontier (full spine close).** Close a second candidate/ShipRun after Thu’s frontier advance so **two ships in sequence** work via the Mon CLI operator path; assert `shipped_frontier` moves twice and attempt statuses stay consistent. Prefer CI e2e that mirrors the spark decomposed sequence (extend `tests/integration/test_cli_dogfood_loop.py` or `backend/tests/test_e2e.py::test_e2e_second_ship_loop_from_advanced_frontier` toward CLI/subprocess coverage, mocked `gh` on ship-run). Verify: `pytest tests/integration/test_cli_dogfood_loop.py backend/tests/test_e2e.py -k "second_ship or dogfood or release_pr" -q`. Status: `todo`.
