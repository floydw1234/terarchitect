# Local Runner Recovery

- Inspect the preserved runner worktree before cleanup: `git status`, `git log --oneline -n 5`, changed files, and focused tests.
- If implementation succeeded but finalization failed, publish the real commit lineage to AgentHub from that worktree and complete the ticket with the actual commit/base hashes.
- Do not report success until both the code state and the ticket/attempt state agree.
