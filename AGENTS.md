# AGENTS.md

## Project Snapshot

- This repository is a Python 3.11+ AI agent runtime for Prism.
- Core runtime flow is recursive and event-driven:

```text
Domain Event
-> Event Router
-> Context Hydration
-> Agent Planner
-> Structured Plan
-> Action Event
-> Action Executor
-> Validator
-> State Mutation or Suggestion
-> Follow-up Event
-> Recursive Processing
```

- Runtime code lives under `src/`.
- YAML prompt definitions live under `prompts/`.
- Architecture docs live under `docs/`.
- Tests and local seed events live under `tests/`.

## Must-follow Architecture

- Keep pure domain models and rules in `src/domain`.
- Keep application orchestration in `src/application`.
- Keep external adapters and registries in `src/infrastructure`.
- Keep runtime entrypoints in `src/interfaces`.
- Keep dependency wiring in `src/app`.
- Keep capability schemas, runtime skill wrappers, and executable tool implementations in `src/capabilities`.
- Do not move prompt text into Python code. LLM-facing instructions belong in YAML under `prompts/`.
- Do not rebuild the architecture or add new top-level layers unless the current boundaries are clearly insufficient.

## Capability Model

The runtime is capability-driven:

```text
Workflow -> selects Skills -> whitelist Tools -> execute Actions
```

### Workflows

- Workflow YAML files live in `prompts/workflows/`.
- Workflows define when and why the agent runs.
- Workflows may define trigger types, required context, required skills, model settings, and workflow constraints.
- Workflows must not directly define tool behavior or detailed reusable reasoning rules.

### Skills

- Skill YAML files live in `prompts/skills/`.
- Skills define reusable reasoning capability and allowed tools.
- Runtime skill objects are instantiated from YAML definitions through `SkillRegistry`.
- Do not add per-skill Python modules with inline prompt text.
- Skills must not execute tools.

### Tools

- Tool YAML files live in `prompts/tools/`.
- Executable tool implementations live in `src/capabilities/tools/`.
- Tool YAML owns LLM-facing descriptions, usage guidance, risk level, approval policy, and input contract.
- Tool Python classes perform concrete execution only.
- Tool Python classes must not define inline prompt descriptions or decide when they are used.

### Registries

- `PromptRegistry` loads typed YAML definitions for workflows, skills, and tools.
- `WorkflowRegistry` loads workflow definitions from workflow prompts.
- `SkillRegistry` loads skill definitions and exposes each skill's tool whitelist.
- `ToolRegistry` instantiates executable tools from YAML tool definitions.
- The planner must reject planned actions that reference tools not allowed by the selected skills.

## Layer Responsibilities

### `src/domain`

- Own pure Pydantic models and domain rules.
- Must not depend on Pydantic AI, Gemini, OCI, file systems, queues, state stores, or network clients.
- Expected contents include events, plans, actions, context, results, suggestions, policies, and domain errors.

### `src/application`

- Own use-case orchestration.
- Route events, hydrate context, create plans, execute one action at a time, validate results, and enqueue follow-up events.
- Keep local recursion and production event handling behavior aligned at the application level.
- Do not put infrastructure-specific OCI, object storage, or network client details here.

### `src/capabilities`

- Own capability definition schemas and runtime wrappers.
- `definitions.py` contains Pydantic models for workflow, skill, and tool YAML definitions.
- `skills.py` wraps YAML-backed skill definitions.
- `tools/` contains executable tool implementations only.

### `src/infrastructure`

- Own external adapters and loading mechanisms.
- LLM integration belongs in `src/infrastructure/llm`.
- Queue adapters belong in `src/infrastructure/queue`.
- State adapters belong in `src/infrastructure/state`.
- Prism API boundary code belongs in `src/infrastructure/prism_api`.
- YAML-backed registries belong in `src/infrastructure/registries`.
- Settings belong in `src/infrastructure/config`.

### `src/interfaces`

- Own runtime entrypoints only.
- `local_entry.py` should remain useful for local recursive execution.
- `oci_function_entry.py` should remain stateless and adapter-backed.
- `cli.py` should stay a thin local helper.

## Runtime And Safety Rules

- Never mutate Prism state directly from raw LLM output.
- The correct execution path is:

```text
LLM -> Structured Plan -> Persisted Plan -> Single Action Execution -> Validation -> Commit or Suggestion
```

- Every workflow must produce an `AgentPlan` before actions execute.
- Execute one `PlannedAction` at a time.
- Preserve idempotency keys for action execution.
- Validate expected entity versions before state mutation.
- Convert stale-context mutations into replan or user-confirmation outcomes.
- Enforce trigger scope. The PM Agent should only react to meaningful product moments, not noisy low-value events.
- Local mode and OCI production mode should differ by adapters, not by application flow.

## Prompt And YAML Rules

- Keep prompt definitions versioned.
- Add workflow prompts under `prompts/workflows/`.
- Add skill prompts under `prompts/skills/`.
- Add tool prompts under `prompts/tools/`.
- Do not duplicate tool descriptions in Python and YAML.
- Do not add broad `Record[str, Any]`-style prompt payloads when the schema is known.
- When adding a tool implementation, add or update the matching tool YAML definition.
- When adding a skill, define its allowed tools explicitly in YAML.
- When adding a workflow, select skills in workflow YAML rather than listing tools directly.

## Testing And Verification

- End implementation tasks with a lint pass.
- Standard checks:

```text
pre-commit run --files <python files under src and tests>
python -m pytest -p no:cacheprovider
```

- Use the active virtual environment when available. On Windows this may be `.venv\Scripts\python`; on POSIX shells this may be `.venv/bin/python`.
- Set `PYTHONPATH=src` using the current shell's syntax if imports fail when running tests directly.
- If `pre-commit` is unavailable, run equivalent `ruff format`, `ruff check`, `python -m compileall -q src tests`, and `pytest` checks.
- Add tests when changing routing, registries, capability boundaries, planner validation, recursion behavior, stale-context handling, or idempotency behavior.
- Avoid adding tests for purely mechanical doc or YAML copy edits unless behavior changes.

## Commit Messages

Use Conventional Commits.

```text
<type>(<scope>): <subject>
```

Allowed types:

```text
feat, fix, refactor, chore, docs, test, style, perf, build, ci, revert
```

Scope should usually be one of:

```text
domain, application, capabilities, infrastructure, interfaces, prompts, runtime, docs, tests, project
```

Rules:

- Use imperative mood, lowercase subject, no trailing period.
- Keep commits focused.
- Do not commit generated output, caches, virtual environments, secrets, or IDE metadata.
- Do not commit `.env`.

Examples:

```text
feat(capabilities): add sprint planning skill definition
fix(application): reject actions outside skill tool whitelist
refactor(infrastructure): split prompt definition models
docs(runtime): clarify local recursion flow
test(registries): cover YAML-backed tool loading
```
