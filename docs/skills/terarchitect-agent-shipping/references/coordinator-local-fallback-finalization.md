# Coordinator Local Fallback Finalization

- If the coordinator or fallback runtime performs finalization, probe that runtime directly for `ah`, auth, and repo/test prerequisites.
- Recover successful local implementation commits before deleting temp repos.
- Distinguish execution success from publication/finalization success when reporting blockers.
