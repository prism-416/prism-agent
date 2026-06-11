from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

STALE_AFTER_DAYS = 14
THIN_DESCRIPTION_LENGTH = 30
OVERLOADED_ACTIVE_ITEM_COUNT = 8
MAX_SIGNAL_EXAMPLES = 15

_OPEN_STATUSES = {"todo", "in_progress", "in_review"}
_CLOSED_STATUSES = {"done", "archived"}


def compute_backlog_signals(
    backlog_items: list[dict[str, Any]],
    member_workloads: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Deterministic refinement signals computed in code, not by the model.

    Date math and threshold judgments are unreliable when delegated to an LLM,
    so the runtime computes them and injects the result as the
    ``backlog_signals`` context entity. The planner's job is deciding how to
    act on these findings, not discovering them.
    """
    current_time = now or datetime.now(UTC)
    items = [item for item in backlog_items if isinstance(item, dict)]
    by_id = {str(item.get("itemId")): item for item in items if item.get("itemId")}

    stale: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    unassigned_urgent: list[dict[str, Any]] = []
    thin_description: list[dict[str, Any]] = []
    parent_mismatch: list[dict[str, Any]] = []

    for item in items:
        status = str(item.get("status") or "")
        if status in _CLOSED_STATUSES:
            continue
        ref = _item_ref(item)

        changed_at = _parse_datetime(item.get("statusChangedAt") or item.get("createdAt"))
        if changed_at is not None and current_time - changed_at > timedelta(days=STALE_AFTER_DAYS):
            stale.append({**ref, "daysIdle": (current_time - changed_at).days})

        due_date = _parse_datetime(item.get("dueDate"))
        if due_date is not None and due_date < current_time:
            overdue.append({**ref, "dueDate": str(item.get("dueDate"))})

        assignees = item.get("assigneeUsernames")
        if str(item.get("priority") or "") in {"high", "urgent"} and not assignees:
            unassigned_urgent.append({**ref, "priority": item.get("priority")})

        if len(str(item.get("description") or "").strip()) < THIN_DESCRIPTION_LENGTH:
            thin_description.append(ref)

        parent = by_id.get(str(item.get("parentId")))
        if parent is not None and str(parent.get("status") or "") in _CLOSED_STATUSES:
            parent_mismatch.append(
                {
                    **ref,
                    "parentItemId": parent.get("itemId"),
                    "parentTitle": parent.get("title"),
                    "parentStatus": parent.get("status"),
                }
            )

    overloaded = [
        {
            "username": workload.get("username"),
            "activeItemCount": workload.get("activeItemCount"),
        }
        for workload in member_workloads
        if isinstance(workload, dict)
        and isinstance(workload.get("activeItemCount"), int)
        and workload["activeItemCount"] >= OVERLOADED_ACTIVE_ITEM_COUNT
    ]

    return {
        "computedAt": current_time.isoformat(),
        "thresholds": {
            "staleAfterDays": STALE_AFTER_DAYS,
            "thinDescriptionLength": THIN_DESCRIPTION_LENGTH,
            "overloadedActiveItemCount": OVERLOADED_ACTIVE_ITEM_COUNT,
        },
        "openItemCount": sum(
            1 for item in items if str(item.get("status") or "") in _OPEN_STATUSES
        ),
        "staleItems": stale[:MAX_SIGNAL_EXAMPLES],
        "overdueItems": overdue[:MAX_SIGNAL_EXAMPLES],
        "unassignedUrgentItems": unassigned_urgent[:MAX_SIGNAL_EXAMPLES],
        "thinDescriptionItems": thin_description[:MAX_SIGNAL_EXAMPLES],
        "closedParentWithOpenChildren": parent_mismatch[:MAX_SIGNAL_EXAMPLES],
        "overloadedMembers": overloaded,
    }


def _item_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "itemId": item.get("itemId"),
        "title": item.get("title"),
        "status": item.get("status"),
    }


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
