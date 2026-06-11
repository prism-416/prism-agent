from __future__ import annotations

from datetime import UTC, datetime, timedelta

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.results import ToolResult


class CreateSprintTool(BaseAgentTool):
    name = "create_sprint"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        workspace_id = str(action.input.get("workspaceId") or context.workspace_id)
        payload = _create_sprint_payload(action.input)
        if self.prism_client and self.prism_client.is_configured:
            requested_by_user_id = _requested_by_user_id(action.input, context)
            if requested_by_user_id:
                payload.setdefault("requestedByUserId", requested_by_user_id)
            output = self.prism_client.create_sprint(workspace_id, payload)
        else:
            output = {
                "sprintId": str(action.input.get("sprintId") or f"local-{action.action_id}"),
                "workspaceId": workspace_id,
                **payload,
            }
        # No follow-up event: nothing routes sprint.created, so emitting one only
        # burns a queue message and an ignored invocation. Traces carry the result.
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
        )


class AddSprintWorkItemsTool(BaseAgentTool):
    """Maps existing work items into a sprint in one action."""

    name = "add_sprint_work_items"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        workspace_id = str(action.input.get("workspaceId") or context.workspace_id)
        sprint_id = str(action.input.get("sprintId") or "")
        item_ids = _valid_item_ids(action.input.get("itemIds"))
        if not item_ids:
            return ToolResult(
                plan_id=action.plan_id,
                action_id=action.action_id,
                tool_name=self.name,
                success=False,
                error=(
                    "add_sprint_work_items requires a non-empty itemIds list of "
                    "existing work item ids."
                ),
            )
        if self.prism_client and self.prism_client.is_configured:
            if not sprint_id:
                # The sprint is usually created earlier in the same plan, so its id
                # is unknown at planning time; resolve to the newest planned sprint.
                sprint_id = _latest_planned_sprint_id(self.prism_client, workspace_id) or ""
            if not sprint_id:
                return ToolResult(
                    plan_id=action.plan_id,
                    action_id=action.action_id,
                    tool_name=self.name,
                    success=False,
                    error="No sprintId given and no planned sprint exists to map items into.",
                )
            payload: dict = {"itemIds": item_ids}
            requested_by_user_id = _requested_by_user_id(action.input, context)
            if requested_by_user_id:
                payload["requestedByUserId"] = requested_by_user_id
            self.prism_client.add_sprint_work_items(workspace_id, sprint_id, payload)
        elif not sprint_id:
            sprint_id = f"local-sprint-{action.action_id}"
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "sprintId": sprint_id,
                "itemIds": item_ids,
                "addedCount": len(item_ids),
            },
        )


def _latest_planned_sprint_id(prism_client, workspace_id: str) -> str | None:
    try:
        sprints = prism_client.get_workspace_sprints(workspace_id)
    except RuntimeError:
        return None
    planned = [
        sprint
        for sprint in sprints
        if isinstance(sprint, dict) and str(sprint.get("status") or "") == "planned"
    ]
    if not planned:
        return None
    planned.sort(key=lambda sprint: str(sprint.get("createdAt") or ""), reverse=True)
    sprint_id = planned[0].get("sprintId")
    return str(sprint_id) if sprint_id else None


def _valid_item_ids(raw: object) -> list[str]:
    from uuid import UUID

    if not isinstance(raw, list):
        return []
    valid: list[str] = []
    for value in raw:
        try:
            item_id = str(UUID(str(value)))
        except ValueError:
            continue
        if item_id not in valid:
            valid.append(item_id)
    return valid


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
    optional_fields = ("goal",)
    for field in optional_fields:
        value = input_data.get(field)
        if value is not None:
            payload[field] = value
    return payload


def _requested_by_user_id(input_data: dict, context: AgentContext) -> str | None:
    requested_by_user_id = input_data.get("requestedByUserId")
    if requested_by_user_id:
        return str(requested_by_user_id)
    queue_pointer = context.source_event.event.payload.get("queue_pointer", {})
    if isinstance(queue_pointer, dict) and queue_pointer.get("requestedByUserId"):
        return str(queue_pointer["requestedByUserId"])
    return None
