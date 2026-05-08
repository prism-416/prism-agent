from __future__ import annotations

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.results import ToolResult


class GenerateSprintReportTool(BaseAgentTool):
    name = "generate_sprint_report"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        sprint = context.entities.get("sprint", {})
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "sprint_id": sprint.get("id") or action.input.get("sprint_id"),
                "report": action.input.get("report") or "Sprint report draft",
            },
        )
