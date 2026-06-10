from __future__ import annotations

import json
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

WORK_ITEM_TREE_NODE_FIELDS = CREATE_WORK_ITEM_FIELDS - {"parentId"}
MAX_WORK_ITEM_TREE_DEPTH = 3
MAX_WORK_ITEM_TREE_ITEMS = 30


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
            requested_by_user_id = _requested_by_user_id(action.input, context)
            if requested_by_user_id:
                payload.setdefault("requestedByUserId", requested_by_user_id)
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


class CreateWorkItemTreeTool(BaseAgentTool):
    """Creates a whole work-item hierarchy in one action.

    The planner emits the full breakdown as nested ``items[].children``; this tool
    walks the tree, creating each parent before its children so the Prism-assigned
    ``itemId`` becomes the children's ``parentId``. One action commits N items in a
    single invocation instead of N queue round-trips.
    """

    name = "create_workitem_tree"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        project_id = str(action.input.get("projectId") or context.project_id or "")
        root_parent_id = action.input.get("parentId")
        items = coerce_work_item_tree_items(action.input)
        error = validate_work_item_tree(items)
        if error:
            return ToolResult(
                plan_id=action.plan_id,
                action_id=action.action_id,
                tool_name=self.name,
                success=False,
                error=error,
            )

        requested_by_user_id = _requested_by_user_id(action.input, context)
        created: list[dict[str, Any]] = []
        for node in items:
            self._create_node(
                node,
                parent_id=str(root_parent_id) if root_parent_id else None,
                depth=1,
                project_id=project_id,
                context=context,
                action=action,
                requested_by_user_id=requested_by_user_id,
                created=created,
            )

        event = DomainEvent(
            event_type="workitem.tree.created",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={
                "itemIds": [item["itemId"] for item in created],
                "createdCount": len(created),
                "rootParentId": root_parent_id,
            },
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "projectId": project_id,
                "createdCount": len(created),
                "items": created,
            },
            emitted_events=[EventEnvelope.wrap(event)],
        )

    def _create_node(
        self,
        node: dict[str, Any],
        *,
        parent_id: str | None,
        depth: int,
        project_id: str,
        context: AgentContext,
        action: PlannedAction,
        requested_by_user_id: str | None,
        created: list[dict[str, Any]],
    ) -> None:
        payload = _filter_payload(node, WORK_ITEM_TREE_NODE_FIELDS)
        payload["title"] = str(node.get("title") or "Generated task")[:100]
        payload.setdefault("description", str(node.get("description") or action.instruction))
        if parent_id:
            payload["parentId"] = parent_id
        if self.prism_client and self.prism_client.is_configured:
            if requested_by_user_id:
                payload.setdefault("requestedByUserId", requested_by_user_id)
            output = self.prism_client.create_work_item(project_id, payload)
        else:
            output = {
                "itemId": f"local-{action.action_id}-{len(created) + 1}",
                "projectId": project_id,
                "workspaceId": context.workspace_id,
                **payload,
            }
        item_id = str(output.get("itemId") or output.get("id") or f"local-{action.action_id}")
        created.append(
            {
                "itemId": item_id,
                "parentId": parent_id,
                "depth": depth,
                "title": payload["title"],
            }
        )
        for child in node.get("children") or []:
            self._create_node(
                child,
                parent_id=item_id,
                depth=depth + 1,
                project_id=project_id,
                context=context,
                action=action,
                requested_by_user_id=requested_by_user_id,
                created=created,
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
            requested_by_user_id = _requested_by_user_id(action.input, context)
            if requested_by_user_id:
                payload.setdefault("requestedByUserId", requested_by_user_id)
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
            payload = {"assigneeUsernames": assignee_usernames}
            requested_by_user_id = _requested_by_user_id(action.input, context)
            if requested_by_user_id:
                payload["requestedByUserId"] = requested_by_user_id
            output = self.prism_client.update_work_item(
                project_id,
                item_id,
                payload,
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
            payload = {"status": status}
            requested_by_user_id = _requested_by_user_id(action.input, context)
            if requested_by_user_id:
                payload["requestedByUserId"] = requested_by_user_id
            output = self.prism_client.update_work_item(project_id, item_id, payload)
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


_TREE_ITEMS_KEYS = ("items", "workItems", "work_items", "tasks", "children")
_TREE_ROOT_ONLY_FIELDS = {"projectId", "parentId", "requestedByUserId"}


def coerce_work_item_tree_items(input_data: dict[str, Any]) -> list[Any] | None:
    """Best-effort extraction of the work item node list from a planned action input.

    Structured planning leaves ``input`` an untyped object, so models sometimes put
    the breakdown under an alternate key, encode it as a JSON string, or emit the
    input as a single node. Accept those shapes instead of failing the action.
    """
    for key in _TREE_ITEMS_KEYS:
        # An input carrying its own title is a single node, so its "children" key
        # belongs to that node rather than being an alias for the items list.
        if key == "children" and str(input_data.get("title") or "").strip():
            break
        value = input_data.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            value = [value]
        if isinstance(value, list) and value:
            return value
    if str(input_data.get("title") or "").strip():
        node = {
            field: value
            for field, value in input_data.items()
            if field not in _TREE_ROOT_ONLY_FIELDS
        }
        return [node]
    return None


def validate_work_item_tree(items: Any) -> str | None:
    """Reject malformed trees before any item is created, so a bad plan fails atomically."""
    if not isinstance(items, list) or not items:
        return "create_workitem_tree requires a non-empty 'items' list."
    count = 0

    def _walk(nodes: list[Any], depth: int) -> str | None:
        nonlocal count
        if depth > MAX_WORK_ITEM_TREE_DEPTH:
            return f"Work item tree exceeds max depth of {MAX_WORK_ITEM_TREE_DEPTH}."
        for node in nodes:
            if not isinstance(node, dict):
                return "Every work item tree node must be an object."
            if not str(node.get("title") or "").strip():
                return "Every work item tree node requires a non-empty title."
            count += 1
            if count > MAX_WORK_ITEM_TREE_ITEMS:
                return f"Work item tree exceeds max size of {MAX_WORK_ITEM_TREE_ITEMS} items."
            children = node.get("children")
            if children is not None and not isinstance(children, list):
                return "Work item tree 'children' must be a list."
            if children:
                error = _walk(children, depth + 1)
                if error:
                    return error
        return None

    return _walk(items, 1)


def _filter_payload(input_data: Any, allowed_fields: set[str]) -> dict[str, Any]:
    if not isinstance(input_data, dict):
        return {}
    return {
        field: value
        for field, value in input_data.items()
        if field in allowed_fields and value is not None
    }


def _requested_by_user_id(input_data: dict, context: AgentContext) -> str | None:
    requested_by_user_id = input_data.get("requestedByUserId")
    if requested_by_user_id:
        return str(requested_by_user_id)
    queue_pointer = context.source_event.event.payload.get("queue_pointer", {})
    if isinstance(queue_pointer, dict) and queue_pointer.get("requestedByUserId"):
        return str(queue_pointer["requestedByUserId"])
    return None
