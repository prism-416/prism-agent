import pytest

from src.models import ToolCall
from src.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_tool_registry_executes_registered_tool() -> None:
    async def sample_tool(arguments: dict[str, object]) -> dict[str, object]:
        return {"echo": arguments.get("value")}

    registry = ToolRegistry()
    registry.register("echo", "Echo value", sample_tool)

    result = await registry.execute(ToolCall(name="echo", arguments={"value": "ok"}))
    assert result.output == {"echo": "ok"}

