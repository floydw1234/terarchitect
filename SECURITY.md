# Security Policy

Terarchitect is currently alpha software. It can orchestrate autonomous coding agents, access repositories, and pass credentials into worker environments, so please treat deployments as sensitive infrastructure.

## Supported versions

| Version | Supported |
|---|---|
| `main` | Best-effort alpha support |
| tagged alpha releases | Best-effort alpha support |

## Reporting a vulnerability

Please do **not** open a public GitHub issue for vulnerabilities involving credential exposure, sandbox escape, repository write access, or private data leakage.

For now, report privately to the repository owner through GitHub. If a dedicated security contact is added later, this file will be updated.

A useful report includes:

- affected commit or release
- deployment mode (`docker compose`, host coordinator, two-box, etc.)
- reproduction steps
- expected vs actual behavior
- redacted logs or screenshots
- whether tokens, source code, logs, or artifacts may have been exposed

## Security expectations for operators

- Prefer a dedicated GitHub token or GitHub App installation with the narrowest repo access possible.
- Never commit `.env`, local databases, logs, AgentHub state, or generated run command files.
- Review worker logs before sharing them; they may include repository names, branch names, prompts, or redacted-but-sensitive context.
- Run untrusted workloads in isolated environments.
- Use single-tenant deployments for any paid/customer use until multi-tenant isolation is explicitly designed and audited.

## Known alpha limitations

- Terarchitect is not yet a hardened multi-tenant SaaS platform.
- Worker containers may require elevated Docker privileges depending on the execution mode.
- Secrets are environment-driven; production deployments should provide their own secret management, backup, monitoring, and access controls.
