# Terarchitect Plans

This folder now has one *authoritative* implementation path and several *reference/theoretical* notes.

## Canonical Reading Order for Codex

Read these first, in order:

1. `agenthub_mvp_plan.md` — **authoritative product scope** for the working AgentHub-native MVP.
2. `agenthub_mvp_execution_checklist.md` — **authoritative implementation order** with exact files, endpoints, tests, and verification commands.

If a reference document conflicts with either of the two files above, the MVP docs win.

## Plan Status Map

### Authoritative

- `agenthub_mvp_plan.md`
  - The actual target scope.
  - Defines what *is* and *is not* part of the MVP.
- `agenthub_mvp_execution_checklist.md`
  - The actual execution sequence.
  - Use this when implementing.

### Reference / Product framing

- `MASTERPLAN.md`
  - High-level product thesis and simplified product framing.
  - Not an execution checklist.
- `UI_plan.md`
  - UI framing for the MVP surfaces and near-future UI direction.
  - Reference only unless explicitly linked from the MVP checklist.
- `ticket_redefinition.md`
  - Conceptual note on tickets becoming intent objects over time.
  - Not required for MVP implementation.

### Theoretical / Archived

- `AGENTHUB-CONVERSION.md`
  - Strategic theory note describing the AgentHub-native shift.
  - Useful for understanding the thesis, but not the source of truth for execution.
- `agent_hub_conversion_plan.md`
  - Archived oversized migration roadmap.
  - Treat as historical/theoretical context only.
- `no_main_idea.md`
  - Explicit future exploration for lab/no-main ideas.
  - Out of MVP scope.

## Working Rule

For implementation work, stay on this spine:

`Ticket -> TicketAttempt -> accepted attempt -> ShipRun -> Ship Room -> one release PR -> shipped_frontier`

Do not expand scope into Composite Workspace, no-main runtime, heavy verification systems, graph side quests, or multi-repo architecture unless the authoritative MVP docs are updated first.

## Operator Path

Use one operator path for swarm projects:

1. agent completes work and publishes a `TicketAttempt`
2. human accepts the attempt
3. compose the wave
4. inspect the resulting `ShipRun`
5. ship/merge at the final wave boundary

Agents and coordinators are the primary users of this system. The UI and human actions remain review/ship boundaries, and the CLI should mirror the ShipRun wave API: `ta ship waves`, `ta ship show`, `ta ship compose`, `ta ship feedback`, and `ta ship merge-pr`.

Ticket-level PR review is not part of swarm mode. The review and ship boundary lives at the wave/`ShipRun` level.

## Related Operational Docs

- `../docs/PHASE1_WORKER_API.md` — worker/coordinator contract
- `../docs/RUNBOOK.md` — operational runbook
