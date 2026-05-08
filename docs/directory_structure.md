# Directory Structure

The source tree keeps application flow, domain state, infrastructure adapters, and runtime capabilities separate:

```text
src/
  app/                 dependency container and wiring
  application/         orchestration use cases and handlers
  capabilities/        YAML-backed capability definitions and executable tools
    tools/             concrete tool implementations only
  domain/              pure event, plan, context, result, and policy models
  infrastructure/      LLM, queue, state, Prism API, config, and registry adapters
  interfaces/          local, CLI, and OCI Function entrypoints
```

Prompt text is not stored under `src/`. YAML owns LLM-facing behavior:

```text
prompts/
  workflows/           when and why the agent runs
  skills/              reasoning capabilities and tool whitelists
  tools/               tool usage guidance, risk, approval, and input contracts
```

This keeps executable tool code from carrying prompt text, and it keeps skill prompt behavior out of Python modules.

