# Open Source Alpha Checklist

Terarchitect is targeting a **v0.1.0-alpha** public release: useful for builders, explicit about sharp edges, and safe to inspect publicly.

## Release position

Terarchitect is an AI-native SDLC orchestrator for coding agents:

- import a GitHub repo into an AgentHub DAG
- run isolated agent attempts against tickets
- inspect and accept attempts at a human boundary
- compose accepted work through Ship Room
- export verified shipped work back to GitHub

This is alpha software. The first public audience is agent-tool builders, devtool hackers, and small teams experimenting with coding agents — not unattended production automation.

## Public alpha readiness gates

Before making the repository public:

- [ ] Working tree is intentionally curated; no accidental local/operator changes are mixed in.
- [ ] Current public tree secret scan passes.
- [ ] Git history scan decision is made:
  - [ ] publish existing history only if acceptable after scan review, or
  - [ ] publish a fresh sanitized Git root.
- [ ] Fresh clone quickstart succeeds from a clean checkout.
- [ ] CI passes on GitHub.
- [ ] README clearly states alpha status and limitations.
- [ ] `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, code of conduct, issue templates, PR template, and CI exist.
- [ ] GitHub description/topics are set.
- [ ] `v0.1.0-alpha` release notes are drafted.

## Current recommended publish strategy

Use a **fresh sanitized public root** unless the existing private history is deliberately kept private or fully reviewed. The current history has had secret-scan findings in old commits, so a fresh public root is the lower-risk path.

## Suggested GitHub metadata

Description:

> AI-native SDLC orchestrator for coding agents: tickets, AgentHub DAG attempts, human review, Ship Room, and GitHub export.

Topics:

- `ai-agents`
- `coding-agents`
- `software-development`
- `sdlc`
- `devtools`
- `agentic-workflows`
- `github`
- `docker`
- `flask`
- `react`
- `postgres`
- `codex`
- `multi-agent`

## v0.1.0-alpha release notes draft

### Highlights

- AgentHub-backed DAG runtime for code attempts and project frontiers.
- Ticket execution through coordinator-managed agent containers.
- Worker support for Codex, OpenCode, and Claude Code modes.
- Human acceptance boundary for attempts.
- Ship Room flow for promotion candidate review and release composition.
- GitHub-first import/export path.
- React operator UI, Flask API, Postgres, and Docker Compose deployment.

### Known limitations

- Alpha APIs and UI flows may change.
- GitHub App integration is not yet the default; token-based configuration is still used.
- Multi-tenant SaaS isolation is out of scope for this release.
- Some failure recovery paths are documented operator workflows rather than polished UI flows.
- Worker containers may require privileged Docker depending on runtime mode.

### Recommended audience

Builders experimenting with AI coding agents who want traceability, review gates, and release composition instead of one-off agent commits.
