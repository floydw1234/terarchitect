Lets dig into this paradime of "no main branch" what if an application was just somehow a mix of all of the most recent commits? Just as an experiment?
This is a genuinely cool thought experiment. It flips the entire Git model on its head.

Instead of:
copy


commit A → commit B → commit C (main)


You get:
copy


      A ──┐
           ├──► Composite State
      B ──┤
           ├──► Composite State
      C ──┘


The "application" isn't a single commit. It's the union of the most recent/active commits, overlaid in real-time.

How it would actually work

1. The "Composite State" engine
You need a layer on top of the DAG that takes N recent commits and merges their diffs into a single virtual filesystem:
- Non-conflicting changes: If Agent A modified utils.py line 10 and Agent B modified utils.py line 50, both changes live. The composite state includes both.
- Conflicting changes: If both modify line 10, you need a strategy:
  - Last-write-wins (timestamp). Simple, but silently drops one agent's work.
  - Functional composition (if they touch the same line, keep both and wrap in a feature flag or conditional). Complex but preserves work.
  - Test-driven selection (run tests against each variant; the one that passes wins). This is actually the most "agent-native" approach.
  - Multi-variant runtime (run both variants in parallel and route traffic based on which is better). Overkill but fascinating.

2. The "Main" becomes a snapshot, not a branch
Main isn't a branch you push to. It's a computed result. You pick a set of commits (e.g., "top 5 most recent from each agent", or "all leaves", or "all commits that passed tests") and the composite state is the union of their diffs applied to the base.

3. What breaks
- Debugging: "Which commit introduced this bug?" — answer: all of them. You lose the clean bisect model.
- Testing: You can't run a test suite against "the composite state" because it's a moving target. Tests become per-variant or per-commit.
- Rollbacks: You don't rollback. You just let newer commits override older ones. The "old" code fades away naturally.
- Documentation: There's no "current version" to document. Docs become a living thing that changes as the composite state changes.

4. What gets better
- Exploration: Agents can diverge wildly and all their work lives simultaneously. No "I need to branch for this feature" friction.
- Parallelism: 10 agents working on the same codebase at the same time? In Git, that's a nightmare. In this model, it's the default.
- Self-healing: If one agent's commit breaks something, another agent's commit can fix it in the same composite state. No PRs, no waiting.
- Discovery: You can browse the DAG and see all the ideas that were tried, not just the ones that made it to main.

The "Research" model

This is actually how scientific research works. You don't have one "main branch" of knowledge. You have a cloud of papers, experiments, and hypotheses. Some support each other, some contradict, some are superseded. The "truth" is the composite of the most relevant/recent work.

Agenthub's DAG is already this. The missing piece is the composite state layer — the thing that says "here's what the application looks like right now, given all the recent commits."

A concrete example

Imagine 3 agents working on a web app:
- Agent A refactors the auth module (clean, good)
- Agent B adds a new feature to the auth module (different approach, also good)
- Agent C fixes a bug in the UI that depends on auth

In Git, you'd have to merge these one by one, dealing with conflicts. In the composite model:
- The app runs with both of Agent A and B's auth changes (maybe A's is the base, B's is layered on top)
- Agent C's UI fix is included
- The composite state is: base + A + B + C
- If B's auth breaks something, you can see exactly which commit caused it and either override it or wait for another agent to fix it

The catch
 (1/2)
The catch is that this only works for research/prototyping. For production software, you need a single source of truth. You need to know exactly what code is running. You can't have "maybe this feature is there, maybe not depending on which agent's commit was most recent."

But for agent swarms doing research, exploration, or rapid iteration, it's brilliant. It turns the codebase into a living, breathing thing that evolves in multiple directions simultaneously, and the "application" is just the current state of that evolution.

Agenthub is the right infrastructure for this. It just needs the composite state layer on top. The DAG is there. The message board is there. What's missing is the thing that says "here's what runs right now." (2/2)

---

## Follow-up framing: future AgentHub Lab mode

This idea makes sense, but it should probably not replace the near-term AgentHub conversion.

The current product direction should stay:

```text
AgentHub attempts -> selected leaves -> coherent release branch -> one release PR -> main
```

That gives Terarchitect a trustworthy shipping path. Production software still needs a stable artifact, an audit boundary, a rollback point, and an exact answer to "what code is running?" For now, `main` should remain the shipped truth.

The no-main idea is still valuable as a future mode:

```text
AgentHub attempts -> virtual composite workspace -> preview/test/explore -> optionally promote to release PR
```

In other words, "no main" should first become an AgentHub-native lab surface, not a production deployment model.

### Three possible modes

1. **Ship Mode**
   - The default Terarchitect workflow.
   - AgentHub is the work DAG.
   - `main` is the shipped ledger.
   - Ship Room composes selected leaves into a release branch.
   - Human reviews one release PR and merges it.

2. **Lab Mode**
   - AgentHub leaves can be overlaid into a temporary composite checkout.
   - Humans and agents can preview multiple attempts together.
   - Tests can run against synthetic combinations.
   - Competing implementations can be compared without committing to a release.
   - A successful composite can be promoted into Ship Mode.

3. **Runtime Mode**
   - The most aggressive future version.
   - A deployed environment runs from a policy-selected composite of AgentHub leaves.
   - This requires very strong reproducibility, observability, rollback, and security controls.
   - This should not be a near-term goal.

### Why this fits AgentHub

AgentHub already has the hard part: a DAG of agent-produced commits, with lineage and collaboration context. A composite workspace would be a layer on top of that DAG that asks:

- Which leaves are eligible?
- Which leaves conflict?
- Which leaves pass tests together?
- Which leaf wins when two edits overlap?
- Which composite should be shown to the human?
- Which composite is good enough to promote into a release PR?

That turns AgentHub from "where agents publish attempts" into "where alternate futures of the codebase can be explored."

### What the first version could be

The first practical version should be narrow:

- Select N AgentHub leaves.
- Create a temporary composite worktree from the current shipped root.
- Apply/merge selected leaves in dependency order.
- Show conflicts, changed files, and test output.
- Let the user save the composite as a candidate `ShipRun`.
- If it passes, open the normal coherent release PR.

That makes the idea useful without abandoning the production guarantees of `main`.

### Product name ideas

- Composite Workspace
- AgentHub Lab
- Virtual Main
- Candidate Runtime
- Preview Universe

The safest name is probably **Composite Workspace**, because it explains what the feature does without implying production should stop having a real shipped branch.

### Key constraint

Do not let this distract from the core conversion.

Build this order:

```text
1. AgentHub attempts
2. TicketAttempt model
3. Ship Room
4. Release PR to main
5. AgentHub root refresh
6. Composite Workspace / Lab Mode
```

The composite idea becomes powerful only after Terarchitect already understands attempts, leaves, validation, dependency waves, ship runs, and root movement.
