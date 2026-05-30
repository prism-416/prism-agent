from __future__ import annotations

from typing import Any

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.results import ToolResult

CREATE_WORK_ITEM_FIELDS = {
    "parentId",
    "title",
    "description",
    "startDate",
    "dueDate",
    "priority",
    "status",
    "assigneeUsernames",
    "labelNames",
}

UPDATE_WORK_ITEM_FIELDS = CREATE_WORK_ITEM_FIELDS


class FindDuplicateWorkItemsTool(BaseAgentTool):
    name = "find_duplicate_workitems"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        work_item = context.entities.get("work_item") or context.entities.get("story", {})
        title = str(action.input.get("title") or work_item.get("title", ""))
        related = context.entities.get("sibling_work_items") or context.entities.get(
            "related_workitems", []
        )
        if not isinstance(related, list):
            related = []
        duplicates = [
            item
            for item in related
            if isinstance(item, dict)
            if title and title.lower() in str(item.get("title", "")).lower()
        ]
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={"duplicates": duplicates},
        )


class CreateWorkItemTool(BaseAgentTool):
    name = "create_workitem"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        project_id = str(action.input.get("projectId") or context.project_id or "")
        payload = _filter_payload(action.input, CREATE_WORK_ITEM_FIELDS)
        payload.setdefault("title", str(action.input.get("title") or "Generated task")[:100])
        payload.setdefault(
            "description", str(action.input.get("description") or action.instruction)
        )
        if self.prism_client and self.prism_client.is_configured:
            output = self.prism_client.create_work_item(project_id, payload)
        else:
            output = {
                "itemId": str(action.input.get("itemId") or f"local-{action.action_id}"),
                "projectId": project_id,
                "workspaceId": context.workspace_id,
                **payload,
            }
        item_id = str(output.get("itemId") or output.get("id") or f"local-{action.action_id}")
        event = DomainEvent(
            event_type="workitem.created",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"itemId": item_id, **output},
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


class UpdateWorkItemTool(BaseAgentTool):
    name = "update_workitem"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        item_id = str(action.input.get("itemId") or action.input.get("id") or "unknown")
        project_id = str(action.input.get("projectId") or context.project_id or "")
        payload = _filter_payload(
            action.input.get("changes", action.input), UPDATE_WORK_ITEM_FIELDS
        )
        if self.prism_client and self.prism_client.is_configured:
            output = self.prism_client.update_work_item(project_id, item_id, payload)
        else:
            output = {"itemId": item_id, "updated": True, **payload}
        event = DomainEvent(
            event_type="workitem.updated",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"itemId": item_id, "changes": payload, "result": output},
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


class AssignWorkItemTool(BaseAgentTool):
    name = "assign_workitem"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        item_id = str(action.input.get("itemId") or "unknown")
        project_id = str(action.input.get("projectId") or context.project_id or "")
        assignee_usernames = action.input.get("assigneeUsernames", [])
        if self.prism_client and self.prism_client.is_configured:
            output = self.prism_client.update_work_item(
                project_id,
                item_id,
                {"assigneeUsernames": assignee_usernames},
            )
        else:
            output = {
                "itemId": item_id,
                "assigneeUsernames": assignee_usernames,
            }
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
        )


class UpdateWorkItemStatusTool(BaseAgentTool):
    name = "update_workitem_status"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        work_item = context.entities.get("work_item") or context.entities.get("workitem", {})
        project_id = str(action.input.get("projectId") or context.project_id or "")
        item_id = str(
            action.input.get("itemId") or work_item.get("itemId") or work_item.get("id", "unknown")
        )
        status = str(action.input.get("status") or "done")
        if self.prism_client and self.prism_client.is_configured:
            output = self.prism_client.update_work_item(project_id, item_id, {"status": status})
        else:
            output = {"itemId": item_id, "status": status}
        event = DomainEvent(
            event_type="workitem.updated",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"itemId": item_id, "status": status},
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


class AddWorkItemCommentTool(BaseAgentTool):
    name = "add_workitem_comment"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "itemId": action.input.get("itemId"),
                "body": action.input.get("body") or action.input.get("comment"),
            },
        )


def _filter_payload(input_data: Any, allowed_fields: set[str]) -> dict[str, Any]:
    if not isinstance(input_data, dict):
        return {}
    return {
        field: value
        for field, value in input_data.items()
        if field in allowed_fields and value is not None
    }
