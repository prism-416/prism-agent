from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.errors import ToolExecutionError
from src.models import ToolCall, ToolResult

ToolFunction = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    handler: ToolFunction


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, name: str, description: str, handler: ToolFunction) -> None:
        self._tools[name] = RegisteredTool(name=name, description=description, handler=handler)

    def list_tools(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        tool = self._tools.get(tool_call.name)
        if tool is None:
            raise ToolExecutionError(f"Tool not found: {tool_call.name}")
        try:
            output = await tool.handler(tool_call.arguments)
            return ToolResult(name=tool_call.name, output=output)
        except Exception as exc:
            raise ToolExecutionError(f"Tool failed: {tool_call.name}: {exc}") from exc

