# GitHub First AgentHub Import Ops

- Confirm the backend sends `repository_url` and `base_ref` to AgentHub and that the response commit is persisted.
- Use a registered AgentHub API key for service-to-service calls inside Docker.
- Keep Docker build contexts clean so rebuilds do not capture runtime state or secrets.
