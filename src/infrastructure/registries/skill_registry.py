from __future__ import annotations

from capabilities.definitions import SkillPromptDefinition
from capabilities.skills import RuntimeSkill
from infrastructure.registries.prompt_registry import PromptRegistry


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, RuntimeSkill] = {}
        self._definitions: dict[str, SkillPromptDefinition] = {}

    @classmethod
    def from_prompt_registry(cls, prompt_registry: PromptRegistry) -> SkillRegistry:
        registry = cls()
        for definition in prompt_registry.list_skills():
            registry.register(RuntimeSkill(definition))
        return registry

    @classmethod
    def with_defaults(cls) -> SkillRegistry:
        return cls.from_prompt_registry(PromptRegistry())

    def register(self, skill: RuntimeSkill) -> None:
        self._skills[skill.id] = skill
        self._definitions[skill.id] = skill.definition

    def get(self, skill_id: str) -> RuntimeSkill:
        if skill_id not in self._skills:
            raise KeyError(f"Skill not registered: {skill_id}")
        return self._skills[skill_id]

    def select(self, skill_ids: list[str]) -> list[RuntimeSkill]:
        return [self.get(skill_id) for skill_id in skill_ids]

    def ids(self) -> list[str]:
        return sorted(self._skills)

    def allowed_tools_for(self, skill_ids: list[str]) -> list[str]:
        allowed_tools: list[str] = []
        seen: set[str] = set()
        for skill in self.select(skill_ids):
            for tool_name in skill.allowed_tools:
                if tool_name not in seen:
                    allowed_tools.append(tool_name)
                    seen.add(tool_name)
        return allowed_tools

    def definition(self, skill_id: str) -> SkillPromptDefinition:
        if skill_id not in self._definitions:
            raise KeyError(f"Skill definition not registered: {skill_id}")
        return self._definitions[skill_id]

    def model_tier(self, skill_id: str) -> str:
        return self.definition(skill_id).model_tier
