# Week Board — 2026-09-07

Weekday cloud-agent slots for the week of Mon 2026-09-07 through Fri 2026-09-11.
Each point fits one weekday PR: a functional product slice toward the MVP spine.

**This week is functional work only — no docs-only, theme, screenshot-for-screenshot, or outreach tasks.**

---

1. **Mon 2026-09-07 — wave→candidate cutover.** Resolve the wave-removal cutover: rebase or redo PR #7 onto current main so candidate/ShipRun is the only operator ship path. Remove any remaining wave-keyed compose/ship contract from the operator surface. Verify: CLI `ta ship` subcommands use candidate/ShipRun exclusively; no wave-keyed ship APIs remain callable. Status: `todo`.

2. **Tue 2026-09-08 — harden accept/choose-winner.** Ensure accepting a validated TicketAttempt marks it as the integrated winner and makes it candidate-eligible without frontier/base drift. If the winner's base differs from current frontier, the accept path must reconcile or reject cleanly. Verify: add or extend a focused test proving accept → winner → candidate-eligible transition with no drift. Status: `done`.

3. **Wed 2026-09-09 — deterministic base selection after ship.** After a ShipRun ships and shipped_frontier advances, subsequent independent jobs must base on the new frontier (or one accepted dependency if specified). Verify: add a test that ships a run, confirms shipped_frontier advances, then spawns a new job and asserts its base equals the new frontier. Status: `todo`.

4. **Thu 2026-09-10 — Ship Room wiring slice.** Connect Ship Room UI to the existing candidate/ShipRun APIs the CLI already exposes (list candidates, candidate detail, compose-candidate, ship-run status). This is wiring, not a UI redesign. Verify: frontend test or manual check that Ship Room displays live candidate/run data from the backend. Status: `todo`.

5. **Fri 2026-09-11 — dogfood-loop blocker fix.** Close one concrete blocker preventing a full dogfood loop on a real local project (reviewFeed or similar): accept validated attempt → compose promotion candidate → ShipRun → open/merge release PR → shipped_frontier advances. Identify the specific API/behavior gap and fix it. Verify: end-to-end test or manual run proving the loop completes. Status: `todo`.
