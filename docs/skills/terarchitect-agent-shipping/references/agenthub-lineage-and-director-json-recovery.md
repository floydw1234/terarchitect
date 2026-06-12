# AgentHub Lineage And Director JSON Recovery

- When AgentHub rejects a publish, inspect ancestry with `git merge-base --is-ancestor` and seed missing lineage before retrying.
- When Director returns malformed JSON, preserve the worker result, then debug the control-plane response separately.
- Avoid claiming an AgentHub publish succeeded if recovery used a different manual import path.
