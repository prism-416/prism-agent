from __future__ import annotations

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.results import ToolResult


class CreateDashboardInsightTool(BaseAgentTool):
    name = "create_dashboard_insight"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        insight_id = f"insight-{action.action_id}"
        event = DomainEvent(
            event_type="insight.created",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"insight_id": insight_id, **action.input},
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={"insight_id": insight_id},
            emitted_events=[EventEnvelope.wrap(event)],
        )
