# Migration to OpenCode

This document describes the Terarchitect worker (OpenCode) and **proxy** setup: OpenCode uses the proxy for chat completions, executes **web_search** in the proxy, and receives **other tools** (Read, Edit, Bash, etc.) back so OpenCode executes them locally.

---

## Goals

1. **Terarchitect**: Invoke OpenCode CLI; capture turn output; keep the existing assess loop (middle agent decides when the ticket is complete).
2. **Proxy**: Support OpenAI-style `/v1/chat/completions` so OpenCode can use it as its LLM endpoint; execute only **web_search** in the proxy; **pass through** any other `tool_calls` (Read, Edit, Bash, etc.) in the response so OpenCode executes them and sends tool results in the next request.

### Testing approach

**Each step in this plan should be tested before moving on.** Use simple existing tests (if present) or small inline scripts to verify behavior. For example: after a proxy change, run a minimal script that POSTs to the proxy and asserts on the response; after a Terarchitect change, run a one-off script that calls the new worker path with a trivial prompt. This keeps the migration incremental and avoids debugging large, untested diffs.

---

## Part 1: Proxy Updates (local-llm/proxy)

**Repository**: `local-llm/proxy` (or wherever `web-search-proxy.py` lives).

### 1.1 Chat completions: pass through non–web_search tool_calls

**Current behavior**: In `chat_completions`, when the model returns any `tool_calls`, the proxy loops over all of them: for `web_search` it runs Brave and builds tool results; for any other tool name it sets `content = "Error: Unknown tool '...'"` and still does a follow-up request to vLLM. So Read/Edit/Bash from the model are never returned to the client; the client (OpenCode) never gets to execute them.

**Required change**: Only execute **web_search** in the proxy. For any other tool_call, **do not** execute or replace with an error; instead, **return the model’s response as-is** to the client so OpenCode can execute those tools and send the next request with tool results.

**Concrete logic** (in `web-search-proxy.py`, `chat_completions` handler, after you have `tool_calls`):

1. Split `tool_calls` into:
   - `web_search_calls`: those with `function.name == "web_search"` (or normalized equivalent).
   - `other_calls`: all others (Read, Edit, Bash, etc.).
2. If `other_calls` is non-empty:
   - **Return immediately** with `JSONResponse(status_code=200, content=first_json)`.
   - Do **not** build `tool_messages`, do **not** do the follow-up request. OpenCode will handle those tool_calls, then send a new request with tool results.
3. If `tool_calls` are **only** web_search:
   - Keep current behavior: execute web_search(es), build `tool_messages`, send follow-up to vLLM, return the follow-up response.

**Optional**: When returning the first response because of `other_calls`, log something like:  
`Passing through response with N non-web_search tool_calls to client (OpenCode will execute them).`

### 1.2 Preserve client tools in the request

When the client (OpenCode) sends a request that already includes `tools` (e.g. Read, Edit, Bash), the proxy should **merge** web_search into that list rather than replace the list (so vLLM sees both client tools and web_search). Current code does:

```python
if "tools" not in body:
    body["tools"] = [WEB_SEARCH_TOOL]
    body["tool_choice"] = "auto"
```

**Required**: If `body` already has `tools`, append or ensure `WEB_SEARCH_TOOL` is in the list (and that `tool_choice` remains sensible, e.g. `"auto"`). Do not remove the client’s tools so vLLM can return both web_search and other tool_calls.

### 1.3 Streaming (optional, later)

If OpenCode uses streaming for chat completions, the proxy’s current streaming path only forwards the request and parses the response as JSON, which is wrong for a stream. Defer streaming support until you confirm OpenCode uses it; if so, the proxy will need to stream the response body and only intercept when the stream contains tool_calls (more involved).

### 1.4 Summary: proxy checklist

- [ ] In `chat_completions`, when `tool_calls` exist, if any tool is not web_search → return `first_json` to client; do not run follow-up.
- [ ] When all `tool_calls` are web_search → execute in proxy, do follow-up, return follow-up response.
- [ ] When client sends `tools`, merge in `WEB_SEARCH_TOOL`; do not drop client tools.
- [ ] (Later) If OpenCode uses streaming, implement proper streaming in the proxy.

---

## Part 2: Terarchitect Codebase Updates

**Repository**: `terarchitect`.

### 2.1 Configuration / environment

**Configuration** (OpenCode only):

- **Worker CLI**: OpenCode. Env vars:
  - `OPENCODE_CMD`: default `opencode`.
  - `OPENCODE_BASE_URL` or reuse a single “LLM base URL” that points at the **proxy** (e.g. `http://localhost:8080/v1`). OpenCode uses OpenAI-compatible providers with a `baseURL`; this should be the proxy’s base so that all chat completion requests from OpenCode go through the proxy (web_search + pass-through of other tools).
  - `OPENCODE_MODEL` or reuse existing model name (e.g. `Qwen/Qwen3-Coder-Next-FP8`) so the proxy/vLLM receive the same model id.
- **OpenCode config**: OpenCode reads provider config from `~/.config/opencode/opencode.json` or env. Ensure the provider used for the worker has `baseURL` set to the proxy URL (e.g. `http://localhost:8080/v1`). Document in README or `.env.example` that for Terarchitect, OpenCode must be configured to use this base URL (and optional model id) so it hits the proxy.


### 2.2 Worker subprocess: OpenCode

`_send_to_opencode(prompt, session_id, project_path, resume)`:

1. **Command**: `opencode run [options] "<prompt>"`.
   - First turn: `opencode run --session <session_id> "<prompt>"` (or equivalent to start a named session).
   - Next turns: `opencode run --session <session_id> "<prompt>"` (same session_id to continue).
   - Alternatively use `--continue` if you always “continue last session” and can guarantee single-agent concurrency; otherwise `--session <session_id>` is safer.
2. **CWD**: Run with `cwd=project_path` so OpenCode operates in the ticket’s project directory.
3. **Env**: Set env so OpenCode uses the proxy for the API:
   - Either set OpenCode’s config via env (e.g. `OPENCODE_CONFIG_CONTENT` or provider base URL env if available), or rely on a pre-configured `~/.config/opencode/opencode.json` that points at the proxy.
4. **Timeout**: Keep a generous timeout (e.g. 300s); OpenCode may do multiple internal rounds (model + tool execution).
5. **Capture**: `capture_output=True`, `text=True`; return `{ "output": stdout or stderr", "error": stderr, "return_code": ... }` so the rest of the agent can stay unchanged.

**Output parsing**: OpenCode’s `opencode run` may print human-readable text or `--format json` events. For the “assess” step you need a single string per turn (the model’s reply or a summary of what OpenCode did). Options:

- Use default (non-JSON) output and treat the whole stdout as the “turn output” for the assess prompt (simplest).
- Or use `--format json` and parse the stream to extract the final assistant message or last text content; then pass that into the assess step. Prefer the simpler approach first; refine if needed.

### 2.3 Call sites

- **process_ticket**: Call `_send_to_opencode` for the initial prompt and for each follow-up prompt; keep the same `session_id` and `resume` semantics (first call = no resume, subsequent = resume same session).
- **process_ticket_review**: Same for the review flow.
- **Logging / trace**: Log messages use "Worker" or "OpenCode"; trace file content reflects which worker ran.

### 2.4 Session and concurrency

- **Session ID**: Keep generating a UUID per Terarchitect “session” (e.g. per ticket run). Pass it to OpenCode via `--session <uuid>` so that all turns for that ticket share one OpenCode session and context.
- **Concurrency**: If multiple tickets can run in parallel, each must use a distinct session_id; OpenCode’s `--session` supports that.

### 2.5 No change (or minimal)

- **Assess loop**: Same as today: after each worker turn, call the existing “agent assess” (separate LLM) with context, prompt history, conversation history (worker outputs), and memories; get `complete` / `summary` / `next_prompt`; if not complete, send `next_prompt` to the worker again.
- **Memory (HippoRAG)**: No change.
- **Branch/PR/finalize**: No change; still keyed off ticket and project_path.

### 2.6 Terarchitect checklist

- [ ] Add env: `OPENCODE_CMD` (or `WORKER_CMD`), base URL for proxy, model id; document OpenCode config (base URL → proxy).
- [ ] Implement `_send_to_opencode(prompt, session_id, project_path, resume)` with `opencode run --session <id> "<prompt>"`, cwd, env, timeout, capture stdout/stderr.
- [ ] Use OpenCode output (default or `--format json`) as the “turn output” for the assess step.
- [ ] In `process_ticket` and `process_ticket_review`, call `_send_to_opencode`.
- [ ] Update logs/traces to reflect OpenCode (or generic “worker”).
- [ ] README / .env.example: document proxy URL, OpenCode install, and that web_search runs in the proxy while other tools run in OpenCode.

---

## Part 3: End-to-end flow (after migration)

1. User moves a ticket to “In Progress” (or triggers review).
2. Terarchitect middle agent loads context, prepares initial prompt, optionally retrieves memory.
3. Agent calls `_send_to_opencode(initial_prompt, session_id, project_path, resume=False)`. OpenCode CLI runs with `--session session_id`, cwd = project_path, and uses the proxy as its LLM base URL.
4. OpenCode sends a chat completion request to the proxy (with tools: its own + web_search from proxy). vLLM may return:
   - **Only text** → proxy returns it; OpenCode shows it and exits; we capture that as “turn output”.
   - **tool_calls including web_search only** → proxy executes web_search, does follow-up to vLLM, returns follow-up to OpenCode; OpenCode may do more rounds or return final text; we capture output.
   - **tool_calls including Read/Edit/Bash** → proxy returns the response unchanged; OpenCode executes those tools locally, sends a new request with tool results; eventually returns; we capture output.
5. Middle agent runs the assess step on the captured output; if not complete, builds `next_prompt` and calls `_send_to_opencode(next_prompt, session_id, project_path, resume=True)` (same session_id). Repeat until assess says complete or max turns.
6. On completion, finalize (commit, push, PR, move to In Review) and index completion into HippoRAG as today.

---

## Part 4: Testing

**Each step should be validated with simple existing tests or inline scripts** (see “Testing approach” above).

- **Proxy (per step)**:
  - After 1.1: Use an inline script (e.g. `curl` or a few lines of Python) that POSTs to the proxy’s `/v1/chat/completions` with a body that mimics vLLM returning (a) only web_search tool_calls, (b) only Read/Edit tool_calls, (c) a mix. Assert: when any non–web_search tool_call is present, the proxy returns the model response as-is; when all are web_search, the proxy executes and returns the follow-up response. Reuse or add a minimal test in the proxy repo if one exists.
  - After 1.2: Same or another script that sends a request with `tools` already set; verify the response from vLLM (or mock) still includes both client tools and web_search where relevant.
- **Terarchitect (per step)**:
  - After 2.1/2.2: Run a small inline script (or existing test) that calls `_send_to_opencode` with a trivial prompt and a temp dir; assert return code and that `output` is non-empty (or matches expectations). No need for a full ticket run at this stage.
  - After 2.3+: Run one full ticket with OpenCode as the worker; confirm it uses the proxy (proxy logs), web_search works (e.g. prompt that asks for a search), and code edits (Read/Edit/Bash) are executed by OpenCode. Confirm the assess loop completes and the ticket moves to In Review.

---

## Summary table

| Component        | Change |
|-----------------|--------|
| **Proxy**       | In `/v1/chat/completions`, return model response to client when any `tool_call` is not web_search; only execute web_search and do follow-up when all tool_calls are web_search. Merge web_search into client’s `tools` instead of replacing. |
| **Terarchitect**| OpenCode CLI invocation (`opencode run --session <id> "prompt"`) with cwd and proxy base URL via `_send_to_opencode`; assess loop, memory, and finalize unchanged. |
| **Config**      | OpenCode provider base URL → proxy (e.g. `http://localhost:8080/v1`). Terarchitect env for worker cmd and optional model/base URL. |

This keeps web_search in the proxy and leaves all other tools to be executed by OpenCode while still using a single proxy endpoint for the LLM.
