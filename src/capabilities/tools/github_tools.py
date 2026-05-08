from __future__ import annotations

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.results import ToolResult


class LinkPullRequestTool(BaseAgentTool):
    name = "link_pull_request"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "pr": context.entities.get("pull_request", {}),
                "workitem_id": action.input.get("workitem_id"),
            },
        )
