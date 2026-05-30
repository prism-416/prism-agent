from __future__ import annotations

from datetime import UTC, datetime, timedelta

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.results import ToolResult


class CreateSprintTool(BaseAgentTool):
    name = "create_sprint"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        workspace_id = str(action.input.get("workspaceId") or context.workspace_id)
        payload = _create_sprint_payload(action.input)
        if self.prism_client and self.prism_client.is_configured:
            output = self.prism_client.create_sprint(workspace_id, payload)
        else:
            output = {
                "sprintId": str(action.input.get("sprintId") or f"local-{action.action_id}"),
                "workspaceId": workspace_id,
                **payload,
            }
        sprint_id = str(output.get("sprintId") or output.get("id") or f"local-{action.action_id}")
        event = DomainEvent(
            event_type="sprint.created",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"sprintId": sprint_id, **output},
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
            emitted_events=[EventEnvelope.wrap(event)],
        )


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


def _create_sprint_payload(input_data: dict) -> dict:
    now = datetime.now(UTC)
    default_start = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    default_end = (
        (now + timedelta(days=14)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    payload = {
        "name": str(input_data.get("name") or "Feature provisioning")[:50],
        "startsAt": input_data.get("startsAt") or default_start,
        "endsAt": input_data.get("endsAt") or default_end,
    }
    optional_fields = ("goal", "status")
    for field in optional_fields:
        value = input_data.get(field)
        if value is not None:
            payload[field] = value
    return payload
