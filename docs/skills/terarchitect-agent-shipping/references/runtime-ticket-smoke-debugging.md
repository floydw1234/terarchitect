# Runtime Ticket Smoke Debugging

- Check for stale pending/running jobs that can starve the intended project.
- Probe the exact runtime that will claim the job for GitHub auth, AgentHub auth, PATH, and worker dependencies.
- Fix queue or runtime preconditions before rerunning the same ticket.
