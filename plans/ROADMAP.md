# Terarchitect Roadmap

**Single source of truth** for product direction, what `main` implements today, known gaps, and out-of-scope work. For day-to-day execution slots see [`week-board.md`](week-board.md). For deployments and operator commands see [`../docs/RUNBOOK.md`](../docs/RUNBOOK.md).

---

## 1. Direction (not yet implemented)

These are owner goals for how Terarchitect should behave. **Nothing below is claimed to exist on `main` unless listed in section 2.**

- Agents operate like a **team of engineers** on a project; a **human sets direction** (projects, tickets, priorities, protected areas).
- **No human PR approvals or merges** for routine promotion: **merge-on-green** when validation and tests pass, with **easy revert** when something regresses.
- A **lead agent per project** coordinates the loop (choose, accept, compose, ship) instead of ad-hoc operator babysitting.
- **Daily digest** of project activity instead of noisy real-time notifications.
- **Human gate only for protected areas**: a minimal per-project path-glob list (not a policy engine, waiver system, or approval language).

Today, the loop is **CLI/API steps** that an agent or operator runs explicitly; section 3 lists what is still missing to match this direction.

---

## 2. Current spine on `main`

End-to-end promotion path:

```text
Ticket → TicketAttempt → evaluate/choose-winner → accept-winner → create-candidate
  → compose ShipRun → ship → shipped_frontier advances
```

| Step | Typical CLI / surface |
|------|------------------------|
| Worker produces attempts | Coordinator + agent container; `TicketAttempt` rows |
| Compare / pick winner | `ta ticket choose-winner <project> <ticket> <attempt>` |
| Integrate winner | `ta ticket accept-winner <project> <ticket> <attempt>` |
| Build promotion set | `ta ship create-candidate <project> …` |
| Compose | `ta ship compose-candidate … --sync` (or compose-run) |
| Inspect | `ta ship run`, `ta ship candidates` |
| Ship | `ta ship ship-run` / `ta ship ship-candidate` |
| One command | `ta ship operator-loop <project> <ticket> <attempt>` |

**Ship targets** (per project, `ship_target`):

- **`agenthub` (default)** — Ship moves `shipped_frontier` to the winning AgentHub commit. No GitHub branch or release PR.
- **`github` (opt-in)** — `ta project set-ship-target <project_id> github` enables GitHub publishing: release PR + merge path; merged tips can be auto-imported into AgentHub. Requires `github_url` and `GITHUB_TOKEN` when cloning or shipping to GitHub.

**Base selection for new work:** dispatch bases on **`shipped_frontier`**, or on **one accepted-unshipped parent ticket’s attempt base** when the ticket depends on that parent.

**Host vs container URLs:** Host-shell `ta` and local shipper use **`TERARCHITECT_API_URL`** and **`TERARCHITECT_AGENTHUB_URL`** (e.g. `http://127.0.0.1:5010` and `http://127.0.0.1:8088`). Compose services use in-network `TERARCHITECT_API_URL` / `AGENTHUB_URL`; do not override container URLs with host values via shared `.env`.

**Operator host (dogfood):** Authorized headless checkout on **spark** at `/home/william/Documents/codingProj/terarchitect` — verify with `ta` + API only ([`docs/RUNBOOK.md`](../docs/RUNBOOK.md) → Headless CLI dogfood).

**CI / agents:** `make ci-python` and [`AGENTS.md`](../AGENTS.md) mirror the GitHub Actions Python smoke job; CI uses concurrency controls on `main`.

---

## 3. Gaps (honest)

- Nothing **automatically** runs choose-winner, accept-winner, create-candidate, compose, or ship when tests go green.
- No **merge-on-green** policy engine or bot merge of GitHub release PRs beyond existing ship-target behavior.
- No **`ta ship revert`** to move `shipped_frontier` back to a previous ShipRun base.
- No **lead-agent** role or project-scoped automation coordinator in product terms.
- No **daily digest** notification product.
- No **protected-path gate** (minimal glob list) before ship/merge.
- **Repeatability:** one live AgentHub-target loop passed on spark (2026-09-28, Smoke 06); needs consistent repeats, not a one-off.

**Later (planned, not implemented):**

- **Attempt pruning:** When several competing attempts (e.g. 3) target the same ticket, non-selected attempts’ AgentHub commits are retained for a configurable per-project period (default 30 days), then pruned. Never prune `shipped_frontier` history or accepted attempts; keep a small permanent record per pruned attempt (ticket, attempt id, outcome/reason).

---

## 4. Out of scope (for the current spine)

Consolidated from the archived MVP plan and masterplan “overbuilt” lists. Do not treat these as prerequisites to ship via AgentHub:

- Composite Workspace or no-main runtime as core product.
- Giant verification/evidence frameworks, canonical event platforms, or graph-specialized verification suites.
- Multi-repository projects, snapshots, blessed states, or cross-repo composition.
- Automated LLM base selection; automatic temporary composition for multiple unshipped dependency parents.
- Browser/replay/mutation/property/LLM-review pipelines; general repair orchestration; rich org dashboards.
- **Policy engines and configurable approval languages** — replaced in direction by a **minimal protected-path gate only** (section 1), not a framework.

Interesting later; not blockers for AgentHub-default shipping.

---

## 5. Status log (PRs #27–#42)

One-line record from merge history on `main` (verify with `git log` / GitHub).

| PR | Status | Summary |
|----|--------|---------|
| #27 | merged | Harden ShipRun boundary: idempotency, shipping recovery, release-PR e2e (pre–AgentHub-default era). |
| #28 | merged | Align CLI winner flow with `shipped_frontier`; backfill tests. |
| #29 | merged | AgentHub boring path: GitHub-free swarm dispatch + finalize hardening. |
| #30 | merged | Wire MVP dependency base selection into job claim/dispatch. |
| #31 | merged | Rebase queued independent tickets on advanced `shipped_frontier` for second ship loop. |
| #32 | merged | Plan week 2026-09-21: dogfood CLI + AgentHub slices. |
| #33 | merged | Align attempt staleness and choose-winner with `shipped_frontier`. |
| #34 | merged | Retarget week board to must-have slices (Sep 21–25). |
| #35 | merged | `ta ship create-candidate` and promotion `next_commands`. |
| #36 | merged | Decomposed CLI dogfood integration test (Wed MH1+MH2). |
| #37 | merged | MH6: worker claim → AgentHub publish → finalize integration. |
| #38 | merged | MH5: dependency ship loop through ShipRun and `shipped_frontier`. |
| #39 | closed | Plan week 2026-09-28 board (superseded by execution PRs). |
| #40 | closed | Spark CLI operator-loop PR; superseded by #41 landing path + follow-ups. |
| #41 | merged | Default `ship_target=agenthub`; GitHub publish opt-in; host AgentHub URL; worker PATH fix. |
| #42 | merged | `scripts/ci-python.sh`, `make ci-python`, `AGENTS.md`, CI workflow concurrency. |

---

## Related docs

| Doc | Role |
|-----|------|
| [`week-board.md`](week-board.md) | Only **execution tracker** (weekday slots). |
| [`../docs/RUNBOOK.md`](../docs/RUNBOOK.md) | **Authoritative ops** (Compose, coordinator, dogfood). |
| [`UI_plan.md`](UI_plan.md), [`ticket_redefinition.md`](ticket_redefinition.md), [`no_main_idea.md`](no_main_idea.md) | Reference notes; direction lives here. |
| [`archive/`](archive/) | Historical plans superseded 2026-09-28. |
