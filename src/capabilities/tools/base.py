from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from capabilities.definitions import ToolPromptDefinition
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.results import ToolResult

if TYPE_CHECKING:
    from infrastructure.prism_api.client import PrismApiClient


class BaseAgentTool(ABC):
    name: str
    input_schema: type[Any] | None = None

    def __init__(
        self,
        definition: ToolPromptDefinition,
        prism_client: PrismApiClient | None = None,
    ) -> None:
        if definition.id != self.name:
            raise ValueError(
                "Tool definition id does not match executable tool name: "
                f"{definition.id} != {self.name}"
            )
        self.definition = definition
        self.prism_client = prism_client

    @property
    def prompt_description(self) -> str:
        return self.definition.description

    @property
    def approval_policy(self) -> str:
        return self.definition.approval_policy

    @property
    def risk_level(self) -> str:
        return self.definition.risk_level

    @abstractmethod
    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        raise NotImplementedError
