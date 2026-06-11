from __future__ import annotations

import re
from typing import Any

from capabilities.tools.workitem_tools import (
    CreateWorkItemTreeTool,
    UpdateWorkItemsBulkTool,
    coerce_work_item_tree_items,
    coerce_work_item_updates,
)
from domain.plans import AgentPlan

MIN_DESCRIPTION_LENGTH = 30
MAX_EXAMPLES_PER_ISSUE = 5
ASSIGNMENT_CONCENTRATION_THRESHOLD = 0.6
MIN_ASSIGNED_LEAVES_FOR_BALANCE_CHECK = 6
MIN_ASSIGNMENT_RATIO = 0.5
MIN_LEAVES_FOR_ASSIGNMENT_RATIO_CHECK = 5
OVERLOADED_ACTIVE_ITEM_COUNT = 8

_BOILERPLATE_HEADINGS = ("acceptance criteria", "context:")
_EXISTING_WORK_ENTITY_KEYS = (
    "project_work_items",
    "backlog_work_items",
    "sibling_work_items",
    "child_work_items",
)
_MEMBER_ENTITY_KEYS = ("project_members", "workspace_members", "member_workloads")
_CLOSED_STATUSES = {"done", "archived"}


def plan_quality_issues(plan: AgentPlan, entities: dict[str, Any]) -> list[str]:
    """Deterministic review of a plan's work item breakdown.

    Returns reviewer-style findings the planner can fix in a revision pass. An
    empty list means the breakdown passed every check. Only soft quality issues
    belong here; hard defects (no items at all) are the planner's defect check.
    """
    issues: list[str] = []
    for action in plan.actions:
        if action.tool_name == CreateWorkItemTreeTool.name:
            items = coerce_work_item_tree_items(action.input)
            if not items:
                continue
            nodes = _flatten(items)
            issues.extend(_duplicate_title_issues(nodes))
            issues.extend(_existing_duplicate_issues(nodes, entities))
            issues.extend(_description_issues(nodes))
            issues.extend(_assignment_issues(nodes, entities))
        elif action.tool_name == UpdateWorkItemsBulkTool.name:
            updates = coerce_work_item_updates(action.input) or []
            issues.extend(
                _target_item_issues(
                    [str(update.get("itemId") or "") for update in updates],
                    entities,
                    action.tool_name,
                    require_unstarted=True,
                )
            )
        elif action.tool_name == "add_sprint_work_items":
            raw_ids = action.input.get("itemIds")
            if isinstance(raw_ids, list):
                issues.extend(
                    _target_item_issues(
                        [str(item_id) for item_id in raw_ids],
                        entities,
                        action.tool_name,
                    )
                )
    return issues


def _known_item_index(entities: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for key in _EXISTING_WORK_ENTITY_KEYS:
        value = entities.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("itemId"):
                index.setdefault(str(item["itemId"]), item)
    return index


def _target_item_issues(
    item_ids: list[str],
    entities: dict[str, Any],
    tool_name: str,
    *,
    require_unstarted: bool = False,
) -> list[str]:
    known = _known_item_index(entities)
    if not known:
        return []
    issues: list[str] = []
    unknown = [item_id for item_id in item_ids if item_id and item_id not in known]
    if unknown:
        issues.append(
            f"The {tool_name} action references item ids that do not exist in the "
            f"hydrated backlog: {_quote_titles(unknown)}. Use itemId values exactly "
            "as they appear in context."
        )
    blocked_statuses = (
        {"in_progress", "in_review", *_CLOSED_STATUSES} if require_unstarted else _CLOSED_STATUSES
    )
    blocked = [
        str(known[item_id].get("title") or item_id)
        for item_id in item_ids
        if item_id in known and str(known[item_id].get("status") or "") in blocked_statuses
    ]
    if blocked:
        scope = (
            "already started or closed; only unstarted (todo) items can be edited — "
            "propose the rest in a suggestion"
            if require_unstarted
            else "already done or archived; leave closed items alone"
        )
        issues.append(
            f"The {tool_name} action touches items that are {scope}: {_quote_titles(blocked)}."
        )
    return issues


def _flatten(items: list[Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nodes.append(item)
        children = item.get("children")
        if isinstance(children, list):
            nodes.extend(_flatten(children))
    return nodes


def _normalize_title(title: Any) -> str:
    return re.sub(r"\s+", " ", str(title or "")).strip().lower()


def _quote_titles(titles: list[str]) -> str:
    shown = ", ".join(f'"{title}"' for title in titles[:MAX_EXAMPLES_PER_ISSUE])
    if len(titles) > MAX_EXAMPLES_PER_ISSUE:
        shown += f" and {len(titles) - MAX_EXAMPLES_PER_ISSUE} more"
    return shown


def _duplicate_title_issues(nodes: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for node in nodes:
        normalized = _normalize_title(node.get("title"))
        if not normalized:
            continue
        if normalized in seen and seen[normalized] not in duplicates:
            duplicates.append(seen[normalized])
        seen.setdefault(normalized, str(node.get("title")))
    if not duplicates:
        return []
    return [
        f"The plan repeats the same task more than once: {_quote_titles(duplicates)}. "
        "Merge duplicates or differentiate their scope."
    ]


def _existing_titles(entities: dict[str, Any]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for key in _EXISTING_WORK_ENTITY_KEYS:
        value = entities.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                normalized = _normalize_title(item.get("title"))
                if normalized:
                    titles.setdefault(normalized, str(item.get("title")))
    return titles


def _existing_duplicate_issues(
    nodes: list[dict[str, Any]],
    entities: dict[str, Any],
) -> list[str]:
    existing = _existing_titles(entities)
    if not existing:
        return []
    duplicated = [
        str(node.get("title")) for node in nodes if _normalize_title(node.get("title")) in existing
    ]
    if not duplicated:
        return []
    return [
        f"These planned tasks already exist as work items: {_quote_titles(duplicated)}. "
        "Drop them, or replace them with tasks for only the missing work that "
        "extends the existing item."
    ]


def _description_issues(nodes: list[dict[str, Any]]) -> list[str]:
    weak: list[str] = []
    for node in nodes:
        title = str(node.get("title") or "")
        description = str(node.get("description") or "").strip()
        if (
            len(description) < MIN_DESCRIPTION_LENGTH
            or _normalize_title(description) == _normalize_title(title)
            or any(heading in description.lower() for heading in _BOILERPLATE_HEADINGS)
        ):
            weak.append(title)
    if not weak:
        return []
    return [
        f"These task descriptions are missing, too thin, boilerplate, or restate the "
        f"title: {_quote_titles(weak)}. Each description must be 2-5 sentences of "
        "plain prose with the concrete behavior, validation, and edge cases the "
        "implementer needs to start coding."
    ]


def _assignment_issues(nodes: list[dict[str, Any]], entities: dict[str, Any]) -> list[str]:
    known_member_count = _known_member_count(entities)
    if known_member_count < 2:
        return []
    issues: list[str] = []
    counts: dict[str, int] = {}
    leaves = 0
    assigned_leaves = 0
    for node in nodes:
        if node.get("children"):
            continue
        leaves += 1
        assignees = node.get("assigneeUsernames")
        if not isinstance(assignees, list) or not assignees:
            continue
        assigned_leaves += 1
        for username in assignees:
            name = str(username or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    if (
        leaves >= MIN_LEAVES_FOR_ASSIGNMENT_RATIO_CHECK
        and assigned_leaves / leaves < MIN_ASSIGNMENT_RATIO
    ):
        available = _members_with_capacity(entities)
        member_hint = f" Members with capacity: {', '.join(available)}." if available else ""
        issues.append(
            f"Only {assigned_leaves} of {leaves} tasks have an assignee. Assigning "
            "work is the project manager's job: give every task whose required "
            "skills match a member's jobNames an owner, using exact username "
            f"values.{member_hint}"
        )
    if (
        known_member_count >= 3
        and assigned_leaves >= MIN_ASSIGNED_LEAVES_FOR_BALANCE_CHECK
        and counts
    ):
        top_username, top_count = max(counts.items(), key=lambda entry: entry[1])
        if top_count / assigned_leaves > ASSIGNMENT_CONCENTRATION_THRESHOLD:
            issues.append(
                f"Assignment is too concentrated: {top_username} holds {top_count} of "
                f"{assigned_leaves} assigned tasks while {known_member_count} members "
                "are available. Rebalance across capable teammates or leave uncertain "
                "tasks unassigned."
            )
    return issues


def _members_with_capacity(entities: dict[str, Any]) -> list[str]:
    workloads = entities.get("member_workloads")
    if not isinstance(workloads, list):
        return []
    available: list[str] = []
    for workload in workloads:
        if not isinstance(workload, dict):
            continue
        username = str(workload.get("username") or "").strip()
        active = workload.get("activeItemCount")
        if username and (not isinstance(active, int) or active < OVERLOADED_ACTIVE_ITEM_COUNT):
            available.append(username)
    return available[:MAX_EXAMPLES_PER_ISSUE]


def _known_member_count(entities: dict[str, Any]) -> int:
    usernames: set[str] = set()
    for key in _MEMBER_ENTITY_KEYS:
        value = entities.get(key)
        if not isinstance(value, list):
            continue
        for member in value:
            if isinstance(member, dict):
                username = str(member.get("username") or "").strip()
                if username:
                    usernames.add(username)
    return len(usernames)
