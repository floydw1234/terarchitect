# Local Codex And Director Debugging

- Verify the worker runtime can run `codex --version` and a trivial `codex exec` command before retrying live tickets.
- Keep sandbox policy explicit when the runtime cannot support the default isolation mode.
- For Director JSON issues, inspect the raw response, reduce schema complexity, and retry with the strictest format the provider actually supports.
