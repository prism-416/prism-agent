from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from capabilities.definitions import (
    PromptDefinition,
    SkillPromptDefinition,
    ToolPromptDefinition,
    WorkflowPromptDefinition,
)


class PromptRegistry:
    def __init__(self, prompts_path: str | Path) -> None:
        self.prompts_path = Path(prompts_path)
        self._prompts: dict[tuple[str, str], PromptDefinition] = {}
        self._workflows: dict[tuple[str, str], WorkflowPromptDefinition] = {}
        self._skills: dict[tuple[str, str], SkillPromptDefinition] = {}
        self._tools: dict[tuple[str, str], ToolPromptDefinition] = {}

    def load(self) -> None:
        if not self.prompts_path.exists():
            raise FileNotFoundError(f"Prompt directory not found: {self.prompts_path}")
        self._prompts.clear()
        self._workflows.clear()
        self._skills.clear()
        self._tools.clear()
        for path in sorted(self.prompts_path.rglob("*.yaml")):
            with path.open("r", encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
            prompt = self._parse_prompt(data, path)
            self._prompts[(prompt.id, prompt.version)] = prompt
            if isinstance(prompt, WorkflowPromptDefinition):
                self._workflows[(prompt.id, prompt.version)] = prompt
            elif isinstance(prompt, SkillPromptDefinition):
                self._skills[(prompt.id, prompt.version)] = prompt
            elif isinstance(prompt, ToolPromptDefinition):
                self._tools[(prompt.id, prompt.version)] = prompt

    def get(self, prompt_id: str, version: str) -> PromptDefinition:
        if not self._prompts:
            self.load()
        key = (prompt_id, version)
        if key not in self._prompts:
            raise KeyError(f"Prompt not registered: {prompt_id}:{version}")
        return self._prompts[key]

    def list(self) -> list[PromptDefinition]:
        if not self._prompts:
            self.load()
        return list(self._prompts.values())

    def get_workflow(self, prompt_id: str, version: str) -> WorkflowPromptDefinition:
        if not self._workflows:
            self.load()
        key = (prompt_id, version)
        if key not in self._workflows:
            raise KeyError(f"Workflow prompt not registered: {prompt_id}:{version}")
        return self._workflows[key]

    def get_skill(self, skill_id: str, version: str = "1.0.0") -> SkillPromptDefinition:
        if not self._skills:
            self.load()
        key = (skill_id, version)
        if key not in self._skills:
            raise KeyError(f"Skill prompt not registered: {skill_id}:{version}")
        return self._skills[key]

    def get_tool(self, tool_id: str, version: str = "1.0.0") -> ToolPromptDefinition:
        if not self._tools:
            self.load()
        key = (tool_id, version)
        if key not in self._tools:
            raise KeyError(f"Tool prompt not registered: {tool_id}:{version}")
        return self._tools[key]

    def list_workflows(self) -> list[WorkflowPromptDefinition]:
        if not self._workflows:
            self.load()
        return list(self._workflows.values())

    def list_skills(self) -> list[SkillPromptDefinition]:
        if not self._skills:
            self.load()
        return list(self._skills.values())

    def list_tools(self) -> list[ToolPromptDefinition]:
        if not self._tools:
            self.load()
        return list(self._tools.values())

    @staticmethod
    def _parse_prompt(data: dict[str, Any], path: Path) -> PromptDefinition:
        kind = data.get("kind") or PromptRegistry._infer_kind(path)
        data = {**data, "kind": kind}
        if kind == "workflow":
            return WorkflowPromptDefinition.model_validate(data)
        if kind == "skill":
            return SkillPromptDefinition.model_validate(data)
        if kind == "tool":
            return ToolPromptDefinition.model_validate(data)
        raise ValueError(f"Unsupported prompt kind in {path}: {kind}")

    @staticmethod
    def _infer_kind(path: Path) -> str:
        parent = path.parent.name
        if parent == "workflows":
            return "workflow"
        if parent == "skills":
            return "skill"
        if parent == "tools":
            return "tool"
        raise ValueError(
            "Prompt kind is required outside prompts/workflows, "
            f"prompts/skills, or prompts/tools: {path}"
        )
