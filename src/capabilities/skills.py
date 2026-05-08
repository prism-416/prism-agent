from __future__ import annotations

from capabilities.definitions import SkillPromptDefinition


class RuntimeSkill:
    id: str
    description: str

    def __init__(self, definition: SkillPromptDefinition) -> None:
        self.definition = definition
        self.id = definition.id
        self.description = definition.description

    @property
    def allowed_tools(self) -> list[str]:
        return self.definition.allowed_tools

    def render_instruction(self) -> str:
        sections = [
            f"Capability: {self.definition.name} ({self.definition.id})",
            self.definition.instruction,
        ]
        if self.definition.reasoning_instructions:
            sections.append(
                "Reasoning instructions:\n"
                + "\n".join(f"- {item}" for item in self.definition.reasoning_instructions)
            )
        if self.definition.quality_expectations:
            sections.append(
                "Quality expectations:\n"
                + "\n".join(f"- {item}" for item in self.definition.quality_expectations)
            )
        if self.definition.guardrails:
            guardrails = "\n".join(
                f"- {key}: {value}" for key, value in self.definition.guardrails.items()
            )
            sections.append(f"Guardrails:\n{guardrails}")
        sections.append("Allowed tools:\n" + "\n".join(f"- {tool}" for tool in self.allowed_tools))
        return "\n".join(sections)
