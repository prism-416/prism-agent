from __future__ import annotations

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.results import ToolResult


class FindDuplicateWorkItemsTool(BaseAgentTool):
    name = "find_duplicate_workitems"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        title = str(action.input.get("title") or context.entities.get("story", {}).get("title", ""))
        related = context.entities.get("related_workitems", [])
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
        workitem_id = str(action.input.get("workitem_id") or f"local-{action.action_id}")
        event = DomainEvent(
            event_type="workitem.created",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"workitem_id": workitem_id, **action.input},
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={"workitem_id": workitem_id, "mode": "simulated_commit"},
            emitted_events=[EventEnvelope.wrap(event)],
        )


class UpdateWorkItemTool(BaseAgentTool):
    name = "update_workitem"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        workitem_id = str(action.input.get("workitem_id") or action.input.get("id") or "unknown")
        event = DomainEvent(
            event_type="workitem.updated",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"workitem_id": workitem_id, "changes": action.input.get("changes", {})},
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={"workitem_id": workitem_id, "updated": True},
            emitted_events=[EventEnvelope.wrap(event)],
        )


class AssignWorkItemTool(BaseAgentTool):
    name = "assign_workitem"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "workitem_id": action.input.get("workitem_id"),
                "assignee_id": action.input.get("assignee_id"),
            },
        )


class UpdateWorkItemStatusTool(BaseAgentTool):
    name = "update_workitem_status"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        workitem_id = str(
            action.input.get("workitem_id")
            or context.entities.get("workitem", {}).get("id", "unknown")
        )
        status = str(action.input.get("status") or "done")
        event = DomainEvent(
            event_type="workitem.updated",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"workitem_id": workitem_id, "status": status},
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={"workitem_id": workitem_id, "status": status},
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
                "workitem_id": action.input.get("workitem_id"),
                "comment": action.input.get("comment"),
            },
        )
