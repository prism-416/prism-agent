from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction, WorkItemLeafDraft, WorkItemUpdateDraft
from domain.context import AgentContext
from domain.plans import AgentPlan
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
MAX_WORK_ITEM_TREE_ITEMS = 100

WORK_ITEM_PRIORITIES = {"low", "medium", "high", "urgent"}
WORK_ITEM_STATUSES = {"todo", "in_progress", "in_review", "done", "archived"}
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_MEMBER_ENTITY_KEYS = ("project_members", "workspace_members", "member_workloads")


SEMANTIC_DUPLICATE_SIMILARITY_THRESHOLD = 0.75
SEMANTIC_DUPLICATE_SEARCH_LIMIT = 8


class FindDuplicateWorkItemsTool(BaseAgentTool):
    """Finds existing work items that overlap a candidate title/description.

    Prefers semantic search over the stored work item embeddings (query embedded
    with the same model the vector workers use); falls back to lexical title
    matching against hydrated context when embeddings or the API are unavailable.
    """

    name = "find_duplicate_workitems"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        work_item = context.entities.get("work_item") or context.entities.get("story", {})
        title = str(action.input.get("title") or work_item.get("title", ""))
        proposed_text = str(action.input.get("description") or "")

        semantic = self._semantic_duplicates(title, proposed_text, context)
        if semantic is not None:
            source_item_id = str(work_item.get("itemId") or work_item.get("id") or "")
            duplicates = [item for item in semantic if str(item.get("itemId")) != source_item_id]
            return ToolResult(
                plan_id=action.plan_id,
                action_id=action.action_id,
                tool_name=self.name,
                success=True,
                output={"duplicates": duplicates, "method": "semantic"},
            )

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
            output={"duplicates": duplicates, "method": "lexical"},
        )

    def _semantic_duplicates(
        self,
        title: str,
        proposed_text: str,
        context: AgentContext,
    ) -> list[dict[str, Any]] | None:
        """Similar items above the duplicate threshold, or None to use the fallback."""
        if not (self.prism_client and self.prism_client.is_configured):
            return None
        if self.embedder is None or not self.embedder.is_configured:
            return None
        query = f"{title}\n{proposed_text}".strip()
        embedding = self.embedder.embed_query(query)
        if embedding is None:
            return None
        try:
            similar = self.prism_client.find_similar_work_items(
                str(context.project_id or ""),
                embedding,
                limit=SEMANTIC_DUPLICATE_SEARCH_LIMIT,
            )
        except RuntimeError:
            return None
        return [
            item
            for item in similar
            if float(item.get("similarity") or 0.0) >= SEMANTIC_DUPLICATE_SIMILARITY_THRESHOLD
        ]


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
        # No follow-up event: nothing routes workitem.created, so emitting one only
        # burns a queue message and an ignored invocation. Traces carry the result.
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
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

        root_parent_id = _valid_uuid_or_none(root_parent_id)
        requested_by_user_id = _requested_by_user_id(action.input, context)
        known_usernames = _known_member_usernames(context)
        for node in items:
            _rollup_assignee_usernames(node, known_usernames)
        created: list[dict[str, Any]] = []
        for node in items:
            self._create_node(
                node,
                parent_id=root_parent_id,
                depth=1,
                project_id=project_id,
                context=context,
                action=action,
                requested_by_user_id=requested_by_user_id,
                known_usernames=known_usernames,
                created=created,
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
        known_usernames: set[str],
        created: list[dict[str, Any]],
    ) -> None:
        payload = _filter_payload(node, WORK_ITEM_TREE_NODE_FIELDS)
        payload["title"] = str(node.get("title") or "Generated task")[:100]
        payload.setdefault("description", str(node.get("description") or action.instruction))
        _normalize_node_payload(payload, known_usernames)
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
                known_usernames=known_usernames,
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
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
        )


MAX_BULK_UPDATES = 50
_BULK_UPDATE_FIELDS = {
    "title",
    "description",
    "priority",
    "status",
    "dueDate",
    "assigneeUsernames",
    "labelNames",
}


MUTABLE_UPDATE_STATUSES = {"todo"}
PROTECTED_UPDATE_FIELDS = {"title", "status"}
_CONTEXT_ITEM_KEYS = (
    "backlog_work_items",
    "project_work_items",
    "sibling_work_items",
    "child_work_items",
)


class UpdateWorkItemsBulkTool(BaseAgentTool):
    """Applies safe field-level updates to many existing work items in one action.

    Existing items may already be in a human's hands, so mutation is policy-gated
    in code, not just in the prompt: only unstarted (todo) items present in the
    hydrated context are touched; titles and statuses never change; descriptions
    are only filled when effectively empty; assignees are only added to
    unassigned items. Everything blocked is reported in ``skipped`` so the
    planner can surface it as a suggestion instead.
    """

    name = "update_workitems_bulk"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        project_id = str(action.input.get("projectId") or context.project_id or "")
        updates = coerce_work_item_updates(action.input)
        error = validate_work_item_updates(updates)
        if error:
            return ToolResult(
                plan_id=action.plan_id,
                action_id=action.action_id,
                tool_name=self.name,
                success=False,
                error=error,
            )

        requested_by_user_id = _requested_by_user_id(action.input, context)
        known_usernames = _known_member_usernames(context)
        item_index = _context_item_index(context)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for update in updates:
            item_id = _valid_uuid_or_none(update.get("itemId"))
            if item_id is None:
                skipped.append({"itemId": update.get("itemId"), "reason": "invalid_item_id"})
                continue
            current = item_index.get(item_id)
            if current is None:
                skipped.append({"itemId": item_id, "reason": "not_in_hydrated_context"})
                continue
            if str(current.get("status") or "") not in MUTABLE_UPDATE_STATUSES:
                skipped.append({"itemId": item_id, "reason": "item_started_or_closed"})
                continue
            payload = _filter_payload(update, _BULK_UPDATE_FIELDS)
            dropped = _drop_protected_fields(payload, current)
            _normalize_node_payload(payload, known_usernames)
            if not payload:
                skipped.append(
                    {"itemId": item_id, "reason": "no_safe_fields", "droppedFields": dropped}
                )
                continue
            if self.prism_client and self.prism_client.is_configured:
                if requested_by_user_id:
                    payload.setdefault("requestedByUserId", requested_by_user_id)
                self.prism_client.update_work_item(project_id, item_id, payload)
            entry: dict[str, Any] = {"itemId": item_id, **payload}
            if dropped:
                entry["droppedFields"] = dropped
            applied.append(entry)

        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "projectId": project_id,
                "updatedCount": len(applied),
                "updates": applied,
                "skipped": skipped,
            },
        )


def _context_item_index(context: AgentContext) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for key in _CONTEXT_ITEM_KEYS:
        value = context.entities.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("itemId"):
                index.setdefault(str(item["itemId"]), item)
    return index


def _drop_protected_fields(payload: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Strip changes that would clobber human work; return what was dropped.

    Titles are an item's identity and statuses are the owner's workflow state, so
    neither is ever bulk-edited. A real description is the author's content —
    only effectively-empty descriptions may be filled in. Assignees are only
    added where nobody owns the item yet.
    """
    dropped = [field for field in PROTECTED_UPDATE_FIELDS if payload.pop(field, None) is not None]
    if "description" in payload and len(str(current.get("description") or "").strip()) >= 30:
        payload.pop("description")
        dropped.append("description")
    if "assigneeUsernames" in payload and current.get("assigneeUsernames"):
        payload.pop("assigneeUsernames")
        dropped.append("assigneeUsernames")
    return dropped


def coerce_work_item_updates(input_data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Best-effort extraction of the update list from a planned action input."""
    for key in ("updates", "workItemUpdates", "work_item_updates", "items", "changes"):
        value = input_data.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            value = [value]
        if isinstance(value, list) and value:
            return [item for item in value if isinstance(item, dict)] or None
    if str(input_data.get("itemId") or "").strip():
        return [
            {
                field: value
                for field, value in input_data.items()
                if field not in _TREE_ROOT_ONLY_FIELDS
            }
        ]
    return None


def validate_work_item_updates(updates: Any) -> str | None:
    if not isinstance(updates, list) or not updates:
        return "update_workitems_bulk requires a non-empty 'updates' list."
    if len(updates) > MAX_BULK_UPDATES:
        return f"Bulk update exceeds max size of {MAX_BULK_UPDATES} items."
    for update in updates:
        if not isinstance(update, dict):
            return "Every update entry must be an object."
        if not str(update.get("itemId") or "").strip():
            return "Every update entry requires an itemId."
        if not any(field in update for field in _BULK_UPDATE_FIELDS):
            return (
                "Every update entry needs at least one field to change "
                "(title, description, priority, status, dueDate, "
                "assigneeUsernames, labelNames)."
            )
    return None


def work_item_update_drafts_to_updates(
    drafts: list[WorkItemUpdateDraft],
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for draft in drafts:
        update: dict[str, Any] = {"itemId": draft.item_id}
        for field, key in (
            ("title", "title"),
            ("description", "description"),
            ("priority", "priority"),
            ("status", "status"),
            ("due_date", "dueDate"),
        ):
            value = getattr(draft, field)
            if value is not None:
                update[key] = value
        if draft.assignee_usernames:
            update["assigneeUsernames"] = list(draft.assignee_usernames)
        if draft.label_names:
            update["labelNames"] = list(draft.label_names)
        updates.append(update)
    return updates


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
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
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

_DRAFT_FIELD_TO_ITEM_KEY = {
    "start_date": "startDate",
    "due_date": "dueDate",
    "priority": "priority",
    "status": "status",
}


def work_item_drafts_to_items(drafts: list[WorkItemLeafDraft]) -> list[dict[str, Any]]:
    """Convert typed planner drafts into the tool's input.items node shape."""
    return [_draft_to_node(draft) for draft in drafts]


def _draft_to_node(draft: WorkItemLeafDraft) -> dict[str, Any]:
    node: dict[str, Any] = {"title": draft.title, "description": draft.description}
    for field, key in _DRAFT_FIELD_TO_ITEM_KEY.items():
        value = getattr(draft, field)
        if value:
            node[key] = value
    if draft.assignee_usernames:
        node["assigneeUsernames"] = list(draft.assignee_usernames)
    if draft.label_names:
        node["labelNames"] = list(draft.label_names)
    children = getattr(draft, "children", None)
    if children:
        node["children"] = [_draft_to_node(child) for child in children]
    return node


def materialize_typed_action_inputs(plan: AgentPlan) -> AgentPlan:
    """Fill untyped tool inputs from the typed PlannedAction fields.

    Structured output cannot populate the untyped action input, so payloads
    travel in typed fields (``work_items``, ``work_item_updates``,
    ``target_item_ids``) and are turned into each tool's input contract right
    after plan generation. An input that already carries a coercible payload
    wins, so explicitly planned inputs are never overwritten.
    """
    updated_plan = plan
    for action in plan.actions:
        updated_input: dict[str, Any] | None = None
        if (
            action.tool_name == CreateWorkItemTreeTool.name
            and action.work_items
            and not coerce_work_item_tree_items(action.input)
        ):
            updated_input = {
                **action.input,
                "items": work_item_drafts_to_items(action.work_items),
            }
        elif (
            action.tool_name == UpdateWorkItemsBulkTool.name
            and action.work_item_updates
            and not coerce_work_item_updates(action.input)
        ):
            updated_input = {
                **action.input,
                "updates": work_item_update_drafts_to_updates(action.work_item_updates),
            }
        elif (
            action.tool_name == "add_sprint_work_items"
            and action.target_item_ids
            and not action.input.get("itemIds")
        ):
            updated_input = {**action.input, "itemIds": list(action.target_item_ids)}
        if updated_input is not None:
            updated_plan = updated_plan.replace_action(
                action.model_copy(update={"input": updated_input})
            )
    return updated_plan


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


def _valid_uuid_or_none(value: Any) -> str | None:
    """Backend parentId is @IsUUID; a hallucinated ref must not fail every root create."""
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except ValueError:
        return None


def _known_member_usernames(context: AgentContext) -> set[str]:
    usernames: set[str] = set()
    for key in _MEMBER_ENTITY_KEYS:
        members = context.entities.get(key)
        if not isinstance(members, list):
            continue
        for member in members:
            if isinstance(member, dict):
                username = str(member.get("username") or "").strip()
                if username:
                    usernames.add(username)
    return usernames


def _clean_usernames(raw: Any, known_usernames: set[str]) -> list[str]:
    usernames = raw if isinstance(raw, list) else [raw]
    cleaned: list[str] = []
    for username in usernames:
        name = str(username or "").strip()
        if name and name not in cleaned and (not known_usernames or name in known_usernames):
            cleaned.append(name)
    return cleaned


def _rollup_assignee_usernames(node: dict[str, Any], known_usernames: set[str]) -> list[str]:
    """Propagate every descendant's assignees onto its ancestors.

    A parent work item must list everyone working under it, so each node's
    assigneeUsernames becomes its own (first) plus the union of its children's,
    computed bottom-up before any item is created.
    """
    merged = _clean_usernames(node.get("assigneeUsernames"), known_usernames)
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        for name in _rollup_assignee_usernames(child, known_usernames):
            if name not in merged:
                merged.append(name)
    if merged:
        node["assigneeUsernames"] = merged
    return merged


def _normalize_node_payload(payload: dict[str, Any], known_usernames: set[str]) -> None:
    """Drop or fix values the Prism work item API would reject with a 400/404.

    One bad enum, date, or hallucinated assignee must not abort the whole tree:
    the API validates priority/status against lowercase enums, dates as strict ISO
    strings, and every assignee username against project membership.
    """
    for field, allowed in (("priority", WORK_ITEM_PRIORITIES), ("status", WORK_ITEM_STATUSES)):
        if field in payload:
            value = str(payload[field]).strip().lower()
            if value in allowed:
                payload[field] = value
            else:
                payload.pop(field)
    for field in ("startDate", "dueDate"):
        if field in payload and not _ISO_DATE_RE.match(str(payload[field]).strip()):
            payload.pop(field)
    if "assigneeUsernames" in payload:
        cleaned = _clean_usernames(payload["assigneeUsernames"], known_usernames)
        if cleaned:
            payload["assigneeUsernames"] = cleaned
        else:
            payload.pop("assigneeUsernames")
    if "labelNames" in payload:
        raw = payload["labelNames"]
        labels = raw if isinstance(raw, list) else [raw]
        cleaned_labels: list[str] = []
        for label in labels:
            name = str(label or "").strip()[:30]
            if name and name not in cleaned_labels:
                cleaned_labels.append(name)
        if cleaned_labels:
            payload["labelNames"] = cleaned_labels
        else:
            payload.pop("labelNames")


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
