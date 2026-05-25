# Terarchitect Masterplan
## Vision: The Senior Engineer's Paradise

> You open a webpage. You describe what you want to build. Tickets appear. You pick the ones you care about. Agents work in an AgentHub DAG. When a tested wave is ready, you ship it to main.

This document is a full architectural and product analysis of where Terarchitect is today, what is broken, what is fundamentally good, and the roadmap to make it a genuinely compelling product.

---

## The Vision in One Paragraph

A senior engineer opens Terarchitect and describes a project - in plain language, a rough brief, or a GitHub URL. The system generates an architecture graph, proposes a backlog of tickets, and asks him to pick, edit, or discard. If he doesn't know what to work on next, AI surfaces the most valuable thing. Once tickets are on the board, agents work in AgentHub: they push attempts to a git DAG, coordinate in ticket channels, and emit testable outputs. Terarchitect composes dependency-safe waves, validates them, and gives the engineer one clear ship control. There is no sprint planning, no ticket grooming ceremonies, no PR noise per agent attempt. Just intent in, working code out.

---

## Strategic Pivot: AgentHub-Native, Not PR-First

The original product spine was "Kanban -> agent job -> PR." That was a good first abstraction because it mapped AI work onto a familiar human workflow. The stronger bet now is "Kanban -> AgentHub attempts -> release branch -> one release PR -> ship to main."

PRs should stop being the internal unit of agent work. They were designed for sparse human-authored changes, not high-volume agent attempts, retries, failures, and parallel exploration. Terarchitect should remove PR-per-ticket from the primary workflow and make AgentHub the execution ledger.

The new product model:

```
architecture graph + tickets define intent
        |
dependency waves define safe parallelism
        |
AgentHub records attempts, leaves, posts, validation, and lineage
        |
Terarchitect selects accepted leaves and composes them into a coherent release branch
        |
human reviews one release PR and ships it to main
```

Detailed implementation plan: see `AGENTHUB-CONVERSION.md`.

---

## Part 1: Architectural Assessment

### What is Genuinely Good

**The core mental model is correct, but the change unit should move from PR to AgentHub.**
Kanban -> agent job -> PR was the right bootstrapping abstraction. The AgentHub-native version is better: a ticket is a unit of intent, an AgentHub attempt is a unit of agent work, and a release PR composed from selected leaves is the unit humans review and ship. Terarchitect got the granularity right from the start; now it should stop forcing every ticket through a human-era PR shape.

**The architecture graph is a real differentiator.**
Almost no other tool encodes the system's component structure as a first-class citizen. Being able to tie a ticket to specific graph nodes and edges means agents get genuine architectural context — not just a vague task description. This is underused right now but is the seed of something powerful. A senior engineer who can express "this ticket touches the auth boundary between Gateway and UserService" gives the agent far better grounding than a plain-text description.

**The Director/Worker separation is sound.**
The Director (strategy, planning, code review, attempt summaries) and Worker (local execution with OpenCode or Claude Code) are a clean division of cognitive labor. This mirrors how good engineers actually work: someone thinks at the system level, someone executes at the file level. This split also makes the system pluggable - swap out the Worker for any tool (Codex, Aider, Gemini) without touching the coordination logic. **Codex is now a supported worker** alongside OpenCode and Claude Code (added May 2026).

**One container per job is the right execution model.**
Reproducibility, isolation, and parallelism come for free. The coordinator's handling of Docker secrets, env rewriting, and host.docker.internal is thoughtful. This is production-grade thinking.

**The queue model is solid.**
`FOR UPDATE SKIP LOCKED` on agent_jobs is correct for parallel claiming. Swarm mode's wave-aware scheduling is genuinely sophisticated. The fact that node/edge conflicts are avoided in job scheduling shows real systems thinking.

**AgentHub is well-structured.**
The Go service is the cleanest component in the codebase — proper middleware, separated handlers, typed DB access. It's a good foundation for the swarm/DAG mode.

---

### What is Architecturally Weak

**~~The backend is a monolith inside a monolith.~~** ✅ *Refactored May 2026*
`backend/api/routes.py` has been decomposed from ~2,500 lines into a proper services layer including attempts, channels, graph, GitHub export, jobs, notes, projects, tickets, shipping, and workspace services. The old per-ticket PR service has been removed. Bugs are now isolatable at the right boundary. Integration tests added for swarm/Docker mode (`tests/integration/test_swarm_docker.py`).

**Auth is broken by design, not just by accident.**
The UI auth / worker auth conflict (where setting `TERARCHITECT_UI_API_KEY` silently breaks all worker endpoints because they live under `/projects/...` instead of `/worker/...`) is not a small bug — it means auth cannot actually be enabled in production without breaking everything. The frontend and CLI don't send auth headers at all. This needs a proper auth layer (e.g. a single middleware that understands request source — UI bearer vs worker bearer vs unauthenticated local dev) before this can be deployed to anyone other than the author.

**Finalization still needs stronger AgentHub-native evidence.**
The PR-per-ticket path has been removed, and normal worker completion now publishes a `TicketAttempt`. The remaining trust problem is validation depth: attempts can become accepted/composed/shipped, but the system still needs richer evidence, timeline, and audit data before a blessed AgentHub state should be treated as production-grade.

**No migration story.**
`db.create_all()` + Docker init SQL means existing databases can't evolve. Every schema change requires wiping state or manual ALTER TABLE. For a tool that's supposed to run persistently across months of a project, this is a critical gap. Alembic or equivalent is required.

**Long-running sync in Flask request handlers.**
Git clone, LLM graph generation, and source-control operations happen synchronously inside request handlers. This means the API blocks on network I/O, which causes cascading timeouts in the frontend and makes the system feel unreliable. These need to be async-queued (Celery, RQ, or just background threads with proper status polling).

**Frontend is a read-mostly UI with no real interaction model.**
KanbanPage.tsx at 1,528 lines is god-component syndrome. More importantly, the frontend is passive — it shows you what's happening but doesn't guide you through what to do next. There's no onboarding, no suggestion surface, no clear entry point for a new user. It's an expert UI that requires prior knowledge of how the system works.

**The coordinator entrypoint is simply missing.**
`coordinator/__main__.py` does not exist. The docs say `python -m coordinator`. This is broken. It's the first thing a new user hits.

---

## Part 2: Gap Analysis — Vision vs Reality

The vision is: describe project -> get tickets -> agents produce attempts -> validated waves -> ship when ready.

Here is where each piece stands today:

| Vision Step | Status | Gap |
|---|---|---|
| Describe project in natural language | Partial | You can create a project, but the "describe it and get a graph" flow is one endpoint that runs synchronously and can fail silently. No guided onboarding. |
| AI generates architecture graph | Partial | The graph generation exists and is unique. But it's buried in a project settings page and requires knowing the right repo URL format. |
| AI proposes tickets from the graph | Missing | There is no "suggest tickets for me" feature. You create tickets manually. This is the biggest UX gap between current state and the vision. |
| Refine / pick / discard tickets | Missing | No bulk ticket UI, no "AI suggest" button, no ability to describe a goal and get a backlog. |
| Tickets auto-complete | Mostly works | The core loop exists. Coordinator -> agent -> AgentHub commit works in swarm mode; the old PR path still exists but should become legacy. |
| Monitor progress without babysitting | Partial | Logs are viewable. Event tailing exists. But polling is manual and there's no push notification model. |
| Ship when ready | Partial | Swarm merge runs exist, but the target architecture should ship selected AgentHub leaves by composing them into a coherent release branch and opening one PR. There is no first-class Ship Room yet. |

---

## Part 3: The Masterplan

### Phase 0 — Minimal Stabilization (only what unblocks AgentHub)

Do only the foundation work needed to iterate safely on the AgentHub conversion. Do not polish the PR workflow.

1. **Fix coordinator/__main__.py** - one file, adds the entrypoint. Without this, the whole system doesn't run.
2. **Add Alembic or an equivalent migration path** - the AgentHub conversion needs schema changes (`ticket_attempts`, merge-run fields, eventual PR table deprecation). Do not continue relying on `db.create_all()` for persistent installs.
3. **Fix auth routing if it blocks local swarm usage** - worker endpoints need to keep working when UI auth is enabled. This can be minimal; full auth productization can wait.
4. **Fix coordinator startup reset** - `max_age_seconds` should be configurable and default to something sane (e.g. 30 minutes, not 0).
5. ✅ **Decompose backend/api/routes.py** *(done May 2026)* - split into focused service modules for graph, jobs, notes, projects, tickets, attempts, channels, GitHub export, shipping, and workspace. Integration test suite added for swarm/Docker mode.

This phase should be short: days, not weeks. Anything that does not directly unblock AgentHub-native execution should move later.

---

### Phase 1 — AgentHub Conversion

This is the main bet and should happen before onboarding polish. If the core workflow is changing from PR-per-ticket to AgentHub attempts and wave shipping, the rest of the product should be built around that shape.

**Flip the default to swarm**
New projects should default to AgentHub/swarm mode. Structured GitHub PR mode can remain temporarily as a legacy/advanced option, but it should no longer drive UI copy, defaults, or roadmap decisions.

**Introduce TicketAttempt**
Stop storing AgentHub commit hashes in `prs.commit_hash`. A ticket can have multiple attempts, and each attempt should carry commit hash, base hash, wave number, agent id, summary, validation status, and attempt status.

**Adopt the moving-root DAG model**
There should be no persistent `swarm` branch in the target architecture. The last shipped `main` commit is the AgentHub root. AgentHub leaves are pending work. Shipping selected leaves records a new `main` commit as the new root and refreshes queued work.

**Replace PR-era ticket states**
In swarm mode, visible states should distinguish:
```
backlog -> queued -> running -> ready -> shipped
                         \-> failed
                         \-> blocked
```
`ready` means an attempt exists and has enough validation to be considered for a wave. `shipped` means the work reached `main`.

**Make AgentHub channels canonical**
Ticket plans, attempt summaries, test failures, human feedback, retry instructions, release-composition events, release PR events, and ship events should be posted to AgentHub channels and rendered by Terarchitect.

**Deprecate PR-per-ticket**
Remove PR-per-ticket from the primary UX. Keep the old path only as a temporary compatibility option until Ship Room reaches parity.

Detailed sequence: see `AGENTHUB-CONVERSION.md`.

---

### Phase 2 — The Execution Loop, Made Reliable

The agents running themselves is the whole product. It needs to work without surprises.

**Ticket lifecycle with honest AgentHub-native states:**
```
backlog -> queued -> running -> attempt_ready -> accepted -> shipped
                              \-> failed (recoverable)
                              \-> blocked (needs human input)
```

`failed` is a first-class state, not a hidden error log. When an agent fails to publish to AgentHub, a release composition conflicts, or tests fail, the ticket or wave shows what happened and offers a retry/fix path.

**Streaming logs in the UI**
Right now logs are polled. They should stream via SSE or WebSocket. When an agent is working, the engineer should see live output — not a blank "running" spinner that resolves 10 minutes later. This is the difference between trusting the system and refreshing the page anxiously.

**Dependency-aware execution**
The graph is already there. Ticket dependencies are already modeled. Waves should continue to be computed from ticket dependencies, while AgentHub records the commit DAG that results from the agents' actual work. Do not confuse these two graphs: ticket dependencies schedule work; AgentHub lineage records what happened.

**Validation before acceptance**
Ticket attempts should move through validation before becoming accepted wave inputs. Validation can start simple: commit exists, summary exists, tests pass if a command is configured, and the attempt can be fetched from AgentHub.

**AgentHub as the primary work ledger**
The current integration treats AgentHub mostly as commit transport. The next step is to make it the ledger for agent attempts, ticket channels, validation events, accepted/rejected attempts, wave membership, release composition, and ship lineage. Terarchitect tickets should define intent; AgentHub should record what agents actually did.

---

### Phase 3 — The Ship Room

The engineer's job should be: inspect composed waves, understand risk, and ship tested work to main. This replaces PR-per-ticket review with an AgentHub-native merge surface.

**Wave inbox**
A dedicated Ship Room shows ready, running, failed, and shipped waves across projects. Each wave shows the tickets it contains, AgentHub attempts, changed files, test results, merge conflicts, board discussion, and a generated risk summary.

**One-click ship to main**
The vision is: "ship selected AgentHub leaves to main whenever you are ready." The Ship Room should resolve selected leaves back to the last shipped root, compose them into a coherent release branch based on current `main`, run tests, open one release PR, merge that PR with `--no-ff`, record the shipped commit as the new AgentHub root, and refresh queued work.

**Feedback through AgentHub channels**
When an engineer asks for changes, the feedback should be posted to the ticket or wave channel in AgentHub and become part of the execution ledger. Agents should respond by creating a new attempt linked to the prior failed/rejected attempt, not by replying to a GitHub PR comment.

---

### Phase 4 — The Onboarding Experience

This should come after the AgentHub-native workflow is stable, otherwise onboarding will teach users the wrong model.

**New Project Flow (current: 4 manual steps -> target: 1 conversation)**

Today: user creates project, pastes a repo URL/path, manually triggers graph generation, manually creates tickets.

Target: user lands on a creation screen, types a description like "I'm building a multi-tenant SaaS on top of a Postgres backend with a React frontend, I want to add email notifications", and the system:
1. Asks for repo/path details only when needed.
2. Generates the architecture graph in the background, shows progress.
3. Proposes 5-10 tickets based on the description and graph.
4. Lets the user pick, rename, reorder, and add more before anything starts.
5. Explains that agents will publish attempts to AgentHub and that the human ships tested waves.

Implementation: a new `/api/projects/<id>/suggest-tickets` endpoint that takes the project description + graph nodes and returns a ranked list of proposed tickets. The frontend gets a new "Backlog Suggestions" panel.

**"I don't know what to work on"**

An AI suggestion surface on the Kanban board: a persistent "Suggested next tickets" panel that analyzes the current graph, completed/shipped waves, failed attempts, AgentHub channel history, and project description to recommend the next 3 things to do.

---

### Phase 5 — Multi-Project Intelligence

Once the single-project experience is excellent, the system can become smarter across projects.

**Cross-project memory**
HippoRAG is already in the codebase. Extend it to capture patterns across projects: "every time we add a new API endpoint, we need to update the OpenAPI spec and add a test — pre-populate those tickets automatically."

**Team mode**
Right now Terarchitect is a single-engineer tool. Adding team assignment, notification routing, and wave ownership makes it viable for small teams. Each engineer still gets their own "paradise" view, but work is coordinated.

**Project templates**
"Start a new FastAPI + React SaaS" generates a known good graph + backlog from a template. Templates are shareable. This dramatically reduces the blank-page problem for new projects.

---

## Part 4: How Close Is This to a Killer App?

Honest answer: the bones are there, but it's about 60% of the way.

**What's already differentiated:**
- The architecture graph as first-class context for agents is genuinely unique. No other tool does this.
- The Director/Worker split with pluggable workers (OpenCode, Claude Code, Codex) is the right abstraction.
- The swarm/AgentHub model for parallel agent work is ahead of the market.
- The "one container per job" execution model is production-grade.

**What kills the experience today:**
- The coordinator doesn't start (missing entrypoint).
- Auth can't be enabled without breaking agents.
- Ticket states still use PR-era language and do not distinguish "AgentHub attempt produced" from "shipped to main."
- There is no onboarding. A new user has no idea what to do first.
- There is no "suggest tickets for me" — which means the vision's most compelling moment (describe your project, get a backlog) doesn't exist yet.
- ~~The backend is an untestable monolith~~ ✅ *resolved May 2026 — services layer extracted, integration tests added.*

**What makes this a killer app (the delta):**
The gap between "pretty good AI coding tool" and "senior engineer's paradise" is almost entirely in the onboarding and the trust. The engineer needs to believe:
1. When I describe something, I get sensible tickets (not done yet).
2. When a ticket says work is ready, there is an AgentHub attempt with a commit, summary, and validation status.
3. When an agent is running, I can see what it's doing (partially done).
4. When I want to ship, there is one clear flow from selected leaves to release PR to main (not done yet).

Fix those four things and this is a killer app.

---

## Summary Roadmap

| Phase | Theme | Key Deliverables | Est. Effort |
|---|---|---|---|
| 0 | Minimal Stabilization | coordinator entrypoint, migration path, auth unblocker, sane stale-job reset | days |
| 1 | AgentHub Conversion | swarm default, TicketAttempt, AgentHub channels, PR-per-ticket deprecated | 2-3 weeks |
| 2 | Execute Reliably | honest AgentHub-native states, streaming logs, dependency-aware waves, validation before acceptance | 2-3 weeks |
| 3 | Ship | Ship Room, leaf selection, coherent release branch, one release PR, AgentHub feedback loop | 2-3 weeks |
| 4 | Onboard | natural-language project creation, AI ticket suggestions, "what should I work on" | 2-3 weeks |
| 5 | Scale | cross-project memory, team mode, project templates | ongoing |

Total to "senior engineer's paradise" v1: approximately 10-12 weeks of focused engineering.

The architecture doesn't need to be torn down. The core model is right. What's needed is hardening the existing pieces, adding the onboarding surface, and making the trust-critical paths AgentHub-native: ticket states, attempt tracking, validation, release PR creation, and ship-to-main.

---

*Last updated: May 2026 - backend services refactor, Codex worker, swarm integration tests, AgentHub-native conversion goal*
