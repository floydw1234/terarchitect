# Configurable Workflow Definitions

Terarchitect supports custom workflow definitions that control the stages a ticket goes through during execution. This replaces the fixed built-in pipeline with a flexible, condition-based stage system.

## Quick Start

Check a workflow file into your repo and pass it when creating/updating the project:

```bash
ta project create my-project --workflow-file .terarchitect/workflow.yaml
ta project update my-project --workflow-file .terarchitect/custom.yaml
```

Remove an explicit workflow file:

```bash
ta project update my-project --clear-workflow-file
```

### Convention-based discovery

If no `--workflow-file` is set but a file exists at one of these paths in the project root, it's auto-discovered (checked in order):

- `.terarchitect/workflow.yaml`
- `.terarchitect/workflow.json`

## Format

Workflows are defined as JSON or YAML with the following schema:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | integer | yes | Must be `1` |
| `stages` | array of stage objects | yes | Ordered list of stages to execute |

### Stage Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique identifier for the stage |
| `type` | string | yes | One of the recognised stage types (see below) |
| `condition` | string or object | no | Controls whether the stage runs for a given ticket. Default: `"always"` |
| `prompt` | string | for `worker_prompt` | Inline prompt text |
| `prompt_key` | string | for `worker_prompt` | Lookup key for a built-in prompt (e.g. `"worker_research_prompt_prefix"`) |
| `uses_task_plan_path` | boolean | no | When true, injects the task plan path as a template variable |
| `max_turns` | integer | for `plan_review` | Max iterations before forcing through (default: 50) |
| `required` | boolean | no | When true, validation rejects conditions that could skip this stage (`"never"` is rejected at validation time; dynamic conditions that resolve to false are caught at runtime) |

### Stage Types

| Type | Purpose | Required Fields |
|------|---------|-----------------|
| `worker_prompt` | Single-turn prompt to the worker agent (research, planning, handoff, etc.) | `prompt` or `prompt_key` |
| `plan_review` | Multi-turn plan review loop between Director and Worker | `max_turns` |
| `execution` | Full execution loop (Director steers Worker through implementation) | — (structural: exactly one required) |
| `finalize` | Commit, publish attempt to AgentHub | — (structural: exactly one required, must be last) |

### Validation Rules

- Exactly **one** `execution` stage and exactly **one** `finalize` stage
- `finalize` must be the **last** stage
- `finalize` must come **after** `execution`
- All stage IDs must be unique
- Stages with `required: true` cannot have `"condition": "never"`
- At runtime, required stages skipped by conditions raise an error

## Conditions

Conditions control per-ticket stage execution. They can be a string or a nested object.

### String Conditions

| Value | Behaviour |
|-------|-----------|
| `"always"` | Stage always runs (default) |
| `"never"` | Stage never runs |

### Object Conditions

| Key | Value Type | Behaviour |
|-----|------------|-----------|
| `title_equals` | string | Runs if `ticket.title` exactly matches |
| `title_contains` | string | Runs if `ticket.title` contains substring (case-insensitive) |
| `description_contains` | string | Runs if `ticket.description` contains substring (case-insensitive) |
| `not` | condition object | Inverts inner condition |
| `all` | array of conditions | Runs if **all** inner conditions match |
| `any` | array of conditions | Runs if **any** inner condition matches |

### Example Conditions

```yaml
# Only run this stage for setup tickets
condition:
  title_equals: "Project setup"

# Skip this stage for setup tickets
condition:
  not:
    title_equals: "Project setup"

# Compound: only for security-related tickets
condition:
  all:
    - title_contains: "security"
    - description_contains: "audit"
```

## Complete Examples

### Custom workflow with research, handoff, and required preflight

```yaml
version: 1
stages:
  - id: research
    type: worker_prompt
    prompt: "Research the approach before implementation."
    condition:
      not:
        title_equals: "Project setup"

  - id: preflight
    type: worker_prompt
    prompt: "Run preflight checks and verify dependencies."
    required: true

  - id: planning
    type: plan_review
    max_turns: 10

  - id: work
    type: execution

  - id: summary
    type: worker_prompt
    prompt: |
      Summarise all changes made during execution.
      Include file changed, key decisions, and any remaining issues.

  - id: push
    type: finalize
```

### Minimal workflow (just implement and ship)

```yaml
version: 1
stages:
  - id: work
    type: execution
  - id: push
    type: finalize
```

## Default Workflow

If no workflow file is provided (either explicitly or via convention), Terarchitect uses the default 6-stage workflow:

1. **research** (`worker_prompt`) — skip for Project Setup tickets
2. **planning** (`worker_prompt`) — skip for Project Setup tickets
3. **plan_review** (`plan_review`) — skip for Project Setup tickets
4. **setup_prompt** (`worker_prompt`) — only for Project Setup tickets
5. **execution** (`execution`) — always runs
6. **finalize** (`finalize`) — always runs (last)

### Condition validation

Workflow conditions are validated at **definition-load time** (when a ticket begins processing). Invalid conditions raise a clear `ValueError`:

| Bad condition | Error |
|---|---|
| `"condition: maybe"` | `unknown condition string 'maybe'` |
| `condition: {bogus: true}` | `unknown condition key(s): 'bogus'` |
| `condition: {}` | `condition dict is empty` |
| `condition: {not: ..., all: ...}` | `condition has conflicting keys: not, all` |
| `condition: {all: not_a_list}` | `condition 'all' must be a list` |

You can also validate a workflow definition **without running a ticket** via the API:

```bash
curl -X POST http://localhost:3939/api/validate-workflow \
  -H 'Content-Type: application/json' \
  -d '{"content": "version: 1\nstages:\n  - id: work\n    type: execution\n  - id: push\n    type: finalize"}'
```

Returns:
```json
{"valid": true, "stage_count": 2, "stages": [{"id": "work", "type": "execution", "required": false}, ...]}
```

Or with a bad condition:
```json
{"valid": false, "error": "unknown condition key(s): 'bogus'"}
```