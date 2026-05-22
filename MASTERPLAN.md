# Terarchitect Masterplan
## Vision: The Senior Engineer's Paradise

> You open a webpage. You describe what you want to build. Tickets appear. You pick the ones you care about. AI handles the rest. When you're ready, you merge.

This document is a full architectural and product analysis of where Terarchitect is today, what is broken, what is fundamentally good, and the roadmap to make it a genuinely compelling product.

---

## The Vision in One Paragraph

A senior engineer opens Terarchitect and describes a project — in plain language, a rough brief, or a GitHub URL. The system generates an architecture graph, proposes a backlog of tickets, and asks him to pick, edit, or discard. If he doesn't know what to work on next, AI surfaces the most valuable thing. Once tickets are on the board, they work themselves — agents clone, implement, push, and open PRs without human intervention. The engineer reviews diffs, merges what he likes, and ignores what he doesn't. The feedback loop is instant. There is no sprint planning, no ticket grooming ceremonies, no waiting. Just intent in, working code out.

---

## Part 1: Architectural Assessment

### What is Genuinely Good

**The core mental model is correct.**
Kanban → agent job → PR is the right abstraction. It maps directly to how senior engineers think: a ticket is a unit of work, a PR is a unit of change, and the board is the view into what's happening. Terarchitect got this right from the start. Most "AI coding" tools go too narrow (autocomplete) or too wide (vague autonomous agents). This hits the right level of granularity.

**The architecture graph is a real differentiator.**
Almost no other tool encodes the system's component structure as a first-class citizen. Being able to tie a ticket to specific graph nodes and edges means agents get genuine architectural context — not just a vague task description. This is underused right now but is the seed of something powerful. A senior engineer who can express "this ticket touches the auth boundary between Gateway and UserService" gives the agent far better grounding than a plain-text description.

**The Director/Worker separation is sound.**
The Director (strategy, planning, code review, PR descriptions) and Worker (local execution with OpenCode or Claude Code) are a clean division of cognitive labor. This mirrors how good engineers actually work: someone thinks at the system level, someone executes at the file level. This split also makes the system pluggable — swap out the Worker for any tool (Codex, Aider, Gemini) without touching the coordination logic.

**One container per job is the right execution model.**
Reproducibility, isolation, and parallelism come for free. The coordinator's handling of Docker secrets, env rewriting, and host.docker.internal is thoughtful. This is production-grade thinking.

**The queue model is solid.**
`FOR UPDATE SKIP LOCKED` on agent_jobs is correct for parallel claiming. Swarm mode's wave/merge-run model is genuinely sophisticated. The fact that node/edge conflicts are avoided in job scheduling shows real systems thinking.

**AgentHub is well-structured.**
The Go service is the cleanest component in the codebase — proper middleware, separated handlers, typed DB access. It's a good foundation for the swarm/DAG mode.

---

### What is Architecturally Weak

**The backend is a monolith inside a monolith.**
`backend/api/routes.py` at 2,497 lines does everything: HTTP routing, business rules, git operations, LLM calls, PR orchestration, swarm scheduling, embedding, RAG, memory, and polling — all in one file. This isn't just a code smell; it means every feature addition creates merge conflicts, every bug is hard to isolate, and the system is impossible to test at the right boundary. The fix is domain decomposition into focused service modules (project service, ticket service, job queue, graph engine, PR service, memory service).

**Auth is broken by design, not just by accident.**
The UI auth / worker auth conflict (where setting `TERARCHITECT_UI_API_KEY` silently breaks all worker endpoints because they live under `/projects/...` instead of `/worker/...`) is not a small bug — it means auth cannot actually be enabled in production without breaking everything. The frontend and CLI don't send auth headers at all. This needs a proper auth layer (e.g. a single middleware that understands request source — UI bearer vs worker bearer vs unauthenticated local dev) before this can be deployed to anyone other than the author.

**Finalization has wrong semantics.**
When `_finalize` completes successfully at the backend level even when git push or PR creation failed, the ticket says "In Review" with no actual PR. This is a trust-destroying bug. A senior engineer who sees "In Review" and goes to check the PR — and there isn't one — loses confidence in the whole system immediately. Completion must be gated on PR creation success, with a `failed` state for recoverable errors.

**No migration story.**
`db.create_all()` + Docker init SQL means existing databases can't evolve. Every schema change requires wiping state or manual ALTER TABLE. For a tool that's supposed to run persistently across months of a project, this is a critical gap. Alembic or equivalent is required.

**Long-running sync in Flask request handlers.**
Git clone, LLM graph generation, and GitHub CLI calls happen synchronously inside request handlers. This means the API blocks on network I/O, which causes cascading timeouts in the frontend and makes the system feel unreliable. These need to be async-queued (Celery, RQ, or just background threads with proper status polling).

**Frontend is a read-mostly UI with no real interaction model.**
KanbanPage.tsx at 1,528 lines is god-component syndrome. More importantly, the frontend is passive — it shows you what's happening but doesn't guide you through what to do next. There's no onboarding, no suggestion surface, no clear entry point for a new user. It's an expert UI that requires prior knowledge of how the system works.

**The coordinator entrypoint is simply missing.**
`coordinator/__main__.py` does not exist. The docs say `python -m coordinator`. This is broken. It's the first thing a new user hits.

---

## Part 2: Gap Analysis — Vision vs Reality

The vision is: describe project → get tickets → auto-complete → merge when ready.

Here is where each piece stands today:

| Vision Step | Status | Gap |
|---|---|---|
| Describe project in natural language | Partial | You can create a project, but the "describe it and get a graph" flow is one endpoint that runs synchronously and can fail silently. No guided onboarding. |
| AI generates architecture graph | Partial | The graph generation exists and is unique. But it's buried in a project settings page and requires knowing the right repo URL format. |
| AI proposes tickets from the graph | Missing | There is no "suggest tickets for me" feature. You create tickets manually. This is the biggest UX gap between current state and the vision. |
| Refine / pick / discard tickets | Missing | No bulk ticket UI, no "AI suggest" button, no ability to describe a goal and get a backlog. |
| Tickets auto-complete | Mostly works | The core loop exists. Coordinator → agent → PR works when auth isn't enabled and the coordinator entrypoint is fixed. |
| Monitor progress without babysitting | Partial | Logs are viewable. Event tailing exists. But polling is manual and there's no push notification model. |
| Merge when ready | Partial | PRs are created. But there's no "merge all approved" view, no review-to-merge flow that respects dependencies. |

---

## Part 3: The Masterplan

### Phase 0 — Stabilize the Foundation (unblock everything else)

These are not features. These are the floor that lets everything else stand.

1. **Fix coordinator/__main__.py** — one file, adds the entrypoint. Without this, the whole system doesn't run.
2. **Fix finalization semantics** — PR creation failure → ticket moves to `failed`, not `in_review`. Recovery path: re-queue or surface error.
3. **Fix auth routing** — worker endpoints need a dedicated auth path that doesn't conflict with UI auth. One auth middleware that accepts either a UI bearer token or a worker bearer token based on request source.
4. **Add Alembic** — schema migrations with version history. Never wipe-and-reinit again.
5. **Move long-running ops out of request handlers** — graph generation, git clone, and GitHub ops become background jobs with status polling endpoints.
6. **Fix coordinator startup reset** — max_age_seconds should be configurable and default to something sane (e.g. 30 minutes, not 0).

This phase is ~2 weeks of focused work. None of it is glamorous, all of it is required.

---

### Phase 1 — The Onboarding Experience

This is where the product becomes approachable to someone who isn't the author.

**New Project Flow (current: 4 manual steps → target: 1 conversation)**

Today: user creates project, pastes GitHub URL, manually triggers graph generation, manually creates tickets.

Target: user lands on a creation screen, types a description like "I'm building a multi-tenant SaaS on top of a Postgres backend with a React frontend, I want to add email notifications", and the system:
1. Asks for the repo URL (or lets you start without one)
2. Generates the architecture graph in the background, shows progress
3. Proposes 5-10 tickets based on the description and graph
4. Lets the user pick, rename, reorder, and add more before anything starts

Implementation: a new `/api/projects/<id>/suggest-tickets` endpoint that takes the project description + graph nodes and returns a ranked list of proposed tickets. The frontend gets a new "Backlog Suggestions" panel.

**"I don't know what to work on"**

An AI suggestion surface on the Kanban board: a persistent "Suggested next tickets" panel that analyzes the current graph, completed tickets, open PRs, and project description to recommend the next 3 things to do. Senior engineers know what they want — but on a bad day, having a smart system say "hey, your auth boundary has no error handling and 3 open tickets depend on it" is genuinely useful.

---

### Phase 2 — The Execution Loop, Made Reliable

The agents running themselves is the whole product. It needs to work without surprises.

**Ticket lifecycle with honest states:**
```
todo → in_progress → [running] → in_review → merged
                              ↘ failed (recoverable)
                              ↘ blocked (needs human input)
```

`failed` is a first-class state, not a hidden error log. When an agent fails to push or create a PR, the ticket shows what happened and offers a retry button.

**Streaming logs in the UI**
Right now logs are polled. They should stream via SSE or WebSocket. When an agent is working, the engineer should see live output — not a blank "running" spinner that resolves 10 minutes later. This is the difference between trusting the system and refreshing the page anxiously.

**Dependency-aware execution**
The graph is already there. Ticket dependencies (parent/child) are already modeled. The coordinator needs to use this — don't start a ticket if its parent hasn't merged. This prevents agents from working on conflicting assumptions.

**AgentHub as the primary code transport**
In swarm mode, agents push to AgentHub's git DAG rather than directly to GitHub. This is architecturally cleaner — it decouples the agent execution from the external repo and lets the system validate, test, and compose changes before they hit origin. Long-term, this is the path to parallel agents that don't stomp each other.

---

### Phase 3 — The Review and Merge Experience

The engineer's job should be: look at PRs, merge what's good, push back on what isn't.

**Review inbox**
A dedicated "Review" page that shows all open PRs across all projects, ranked by staleness. Each PR shows: the ticket it came from, the diff summary (AI-generated), and quick actions: Approve, Request Changes, Merge.

**One-click merge to master**
The vision is: "merge the agenthub branch into master whenever you want to update." This needs a first-class UI action. In Docker mode, this is `gh pr merge`. In AgentHub/swarm mode, this is a merge-run that composes multiple agent branches into a coherent commit.

**Review-driven feedback loop**
When an engineer comments on a PR, the agent should respond — not just log the comment. The review polling + re-queue mechanism already exists in the codebase. It needs to be reliable and surfaced clearly: "Agents respond to your review comments" should be a visible feature, not a hidden behavior.

---

### Phase 4 — Multi-Project Intelligence

Once the single-project experience is excellent, the system can become smarter across projects.

**Cross-project memory**
HippoRAG is already in the codebase. Extend it to capture patterns across projects: "every time we add a new API endpoint, we need to update the OpenAPI spec and add a test — pre-populate those tickets automatically."

**Team mode**
Right now Terarchitect is a single-engineer tool. Adding team assignment (which engineer reviews which tickets, notification routing) makes it viable for small teams. Each engineer still gets their own "paradise" view, but work is coordinated.

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
- Tickets incorrectly claim to be "in review" when no PR was created.
- There is no onboarding. A new user has no idea what to do first.
- There is no "suggest tickets for me" — which means the vision's most compelling moment (describe your project, get a backlog) doesn't exist yet.

**What makes this a killer app (the delta):**
The gap between "pretty good AI coding tool" and "senior engineer's paradise" is almost entirely in the onboarding and the trust. The engineer needs to believe:
1. When I describe something, I get sensible tickets (not done yet).
2. When a ticket says "in review," there is a real PR (broken today).
3. When an agent is running, I can see what it's doing (partially done).
4. When I want to merge, it's one button (not done yet).

Fix those four things and this is a killer app.

---

## Summary Roadmap

| Phase | Theme | Key Deliverables | Est. Effort |
|---|---|---|---|
| 0 | Stabilize | coordinator entrypoint, finalization fix, auth routing, Alembic, async ops | 2 weeks |
| 1 | Onboard | natural-language project creation, AI ticket suggestions, "what should I work on" | 3 weeks |
| 2 | Execute | honest ticket states, streaming logs, dependency-aware scheduling, AgentHub as primary transport | 3 weeks |
| 3 | Review & Merge | review inbox, one-click merge, PR→agent feedback loop reliability | 2 weeks |
| 4 | Scale | cross-project memory, team mode, project templates | ongoing |

Total to "senior engineer's paradise" v1: approximately 10 weeks of focused engineering.

The architecture doesn't need to be torn down. The core model is right. What's needed is hardening the existing pieces, adding the onboarding surface, and making the trust-critical paths (ticket states, PR creation, logs) bulletproof.

---

*Last updated: May 2026*
