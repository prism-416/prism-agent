from __future__ import annotations

from capabilities.definitions import ToolPromptDefinition
from capabilities.tools.base import BaseAgentTool
from capabilities.tools.dashboard_tools import CreateDashboardInsightTool
from capabilities.tools.github_tools import LinkPullRequestTool
from capabilities.tools.sprint_tools import GenerateSprintReportTool
from capabilities.tools.suggestion_tools import CreateAgentSuggestionTool
from capabilities.tools.workitem_tools import (
    AddWorkItemCommentTool,
    AssignWorkItemTool,
    CreateWorkItemTool,
    FindDuplicateWorkItemsTool,
    UpdateWorkItemStatusTool,
    UpdateWorkItemTool,
)
from infrastructure.registries.prompt_registry import PromptRegistry


class ToolRegistry:
    TOOL_IMPLEMENTATIONS: dict[str, type[BaseAgentTool]] = {
        CreateAgentSuggestionTool.name: CreateAgentSuggestionTool,
        FindDuplicateWorkItemsTool.name: FindDuplicateWorkItemsTool,
        CreateWorkItemTool.name: CreateWorkItemTool,
        UpdateWorkItemTool.name: UpdateWorkItemTool,
        AssignWorkItemTool.name: AssignWorkItemTool,
        UpdateWorkItemStatusTool.name: UpdateWorkItemStatusTool,
        AddWorkItemCommentTool.name: AddWorkItemCommentTool,
        GenerateSprintReportTool.name: GenerateSprintReportTool,
        LinkPullRequestTool.name: LinkPullRequestTool,
        CreateDashboardInsightTool.name: CreateDashboardInsightTool,
    }

    def __init__(self) -> None:
        self._tools: dict[str, BaseAgentTool] = {}
        self._definitions: dict[str, ToolPromptDefinition] = {}

    @classmethod
    def from_prompt_registry(cls, prompt_registry: PromptRegistry) -> ToolRegistry:
        registry = cls()
        for definition in prompt_registry.list_tools():
            implementation = cls.TOOL_IMPLEMENTATIONS.get(definition.id)
            if implementation is None:
                continue
            registry.register(implementation(definition))
        return registry

    @classmethod
    def with_defaults(cls) -> ToolRegistry:
        return cls.from_prompt_registry(PromptRegistry("prompts"))

    def register(self, tool: BaseAgentTool) -> None:
        self._tools[tool.name] = tool
        self._definitions[tool.name] = tool.definition

    def get(self, name: str) -> BaseAgentTool:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        return self._tools[name]

    def select(self, names: list[str]) -> list[BaseAgentTool]:
        return [self.get(name) for name in names]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definition(self, name: str) -> ToolPromptDefinition:
        if name not in self._definitions:
            raise KeyError(f"Tool definition not registered: {name}")
        return self._definitions[name]
