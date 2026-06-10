from __future__ import annotations

import re
from typing import Any

from capabilities.tools.workitem_tools import (
    CreateWorkItemTreeTool,
    coerce_work_item_tree_items,
)
from domain.plans import AgentPlan

MIN_DESCRIPTION_LENGTH = 30
MAX_EXAMPLES_PER_ISSUE = 5
ASSIGNMENT_CONCENTRATION_THRESHOLD = 0.6
MIN_ASSIGNED_LEAVES_FOR_BALANCE_CHECK = 6

_BOILERPLATE_HEADINGS = ("acceptance criteria", "context:")
_EXISTING_WORK_ENTITY_KEYS = ("project_work_items", "sibling_work_items", "child_work_items")
_MEMBER_ENTITY_KEYS = ("project_members", "workspace_members", "member_workloads")


def plan_quality_issues(plan: AgentPlan, entities: dict[str, Any]) -> list[str]:
    """Deterministic review of a plan's work item breakdown.

    Returns reviewer-style findings the planner can fix in a revision pass. An
    empty list means the breakdown passed every check. Only soft quality issues
    belong here; hard defects (no items at all) are the planner's defect check.
    """
    issues: list[str] = []
    for action in plan.actions:
        if action.tool_name != CreateWorkItemTreeTool.name:
            continue
        items = coerce_work_item_tree_items(action.input)
        if not items:
            continue
        nodes = _flatten(items)
        issues.extend(_duplicate_title_issues(nodes))
        issues.extend(_existing_duplicate_issues(nodes, entities))
        issues.extend(_description_issues(nodes))
        issues.extend(_assignment_issues(nodes, entities))
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
    if known_member_count < 3:
        return []
    counts: dict[str, int] = {}
    assigned_leaves = 0
    for node in nodes:
        if node.get("children"):
            continue
        assignees = node.get("assigneeUsernames")
        if not isinstance(assignees, list) or not assignees:
            continue
        assigned_leaves += 1
        for username in assignees:
            name = str(username or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    if assigned_leaves < MIN_ASSIGNED_LEAVES_FOR_BALANCE_CHECK or not counts:
        return []
    top_username, top_count = max(counts.items(), key=lambda entry: entry[1])
    if top_count / assigned_leaves <= ASSIGNMENT_CONCENTRATION_THRESHOLD:
        return []
    return [
        f"Assignment is too concentrated: {top_username} holds {top_count} of "
        f"{assigned_leaves} assigned tasks while {known_member_count} members are "
        "available. Rebalance across capable teammates or leave uncertain tasks "
        "unassigned."
    ]


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
