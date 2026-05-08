from __future__ import annotations

from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.results import ToolResult
from infrastructure.registries.tool_registry import ToolRegistry


class Executor:
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    def execute_one(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        tool = self.tool_registry.get(action.tool_name)
        return tool.execute(action, context)
