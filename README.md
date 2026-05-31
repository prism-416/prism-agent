# prism-agent

`prism-agent` is Prism's recursive, event-driven AI project manager runtime.

The runtime consumes meaningful project events, hydrates context, creates structured plans, executes one action at a time, validates state changes, commits safe mutations or suggestions, and emits follow-up events for recursive processing.

Core flow:

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

The same application flow runs in local memory mode and production OCI mode. Only infrastructure adapters differ.

```text
src/
  app/
  application/
  capabilities/
    tools/
  domain/
  infrastructure/
  interfaces/
prompts/
  workflows/
  skills/
  tools/
tests/
```

The runtime is capability-driven: workflows select skills, skills whitelist tools,
and tool prompt definitions describe LLM-facing usage while executable tool classes
perform concrete actions. See [docs/capability_model.md](docs/capability_model.md)
and [docs/directory_structure.md](docs/directory_structure.md). OCI Function deployment
setup is documented in [docs/oci_function_deployment.md](docs/oci_function_deployment.md).

Run local example:

```bash
python -m interfaces.cli tests/fixtures/seed_events/story_created.json
```
