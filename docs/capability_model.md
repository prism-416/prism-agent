# Capability Model

`prism-agent` is capability-driven and prompt text is YAML-owned:

```text
Workflow -> Skills -> Tools -> Execution
```

Prompts are split by runtime responsibility:

```text
prompts/
  workflows/
  skills/
  tools/
```

Runtime capability code lives together under `src/capabilities/`:

```text
src/capabilities/
  definitions.py   # Pydantic schemas for workflow, skill, and tool YAML
  skills.py        # YAML-backed RuntimeSkill wrapper
  tools/           # executable tool implementations only
```

## Runtime Composition Flow

1. `EventRouter` maps a meaningful trigger to a `WorkflowDefinition`.
2. `WorkflowRegistry` loads workflow definitions from `prompts/workflows/*.yaml`.
3. The selected workflow names its `required_skills`.
4. `SkillRegistry` loads skill definitions from `prompts/skills/*.yaml`.
5. The selected skills define the only allowed tools for the workflow.
6. `ToolRegistry` loads executable tool classes from `src/capabilities/tools/` and instantiates them with prompt definitions from `prompts/tools/*.yaml`.
7. `Planner` builds the allowed tool set from selected skills and rejects plans that reference any other tool.
8. `PydanticAIAgentFactory` composes the final system prompt:

```text
Workflow Prompt
+ Skill Prompt(s)
+ Tool Prompt(s)
+ Runtime Context
```

## Responsibility Boundaries

Workflow prompts define when and why the agent runs: triggers, goal, required context, required skills, and workflow-level constraints.

Skill prompts define reusable reasoning capability: instructions, quality expectations, guardrails, and the tool whitelist. Runtime skill objects are instantiated from these YAML definitions; do not add per-skill Python modules with inline prompt text.

Tool prompts define LLM-facing usage guidance: description, when to use, when not to use, risk level, approval policy, and input contract.

Executable tools contain concrete action code only. They are instantiated with YAML `ToolPromptDefinition` objects and must not define LLM-facing descriptions or usage guidance inline.

## Migration Notes

Old flat prompt fields move as follows:

- `skills` on workflow prompt -> `required_skills` in `prompts/workflows/*.yaml`
- `tools` on workflow prompt -> `allowed_tools` in one or more `prompts/skills/*.yaml`
- detailed reasoning instructions -> skill prompt `reasoning_instructions`
- tool descriptions and approval notes -> `prompts/tools/*.yaml`
- workflow-only goals, context, and constraints stay in workflow prompts

To add a workflow without changing runtime core logic:

1. Add a workflow YAML file under `prompts/workflows/`.
2. Reference existing skills or add new skill YAML under `prompts/skills/`.
3. Ensure each skill whitelists only the concrete tools it may use.
4. Add a tool YAML under `prompts/tools/` only when the executable tool already exists or is added to `ToolRegistry`.
