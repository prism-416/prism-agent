from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from domain.actions import ActionStatus, PlannedAction
from domain.context import AgentContext
from domain.plans import AgentPlan

_AGENT_RUN_ID_KEYS = ("agentRunId", "runId", "agent_run_id")
_AGENT_RUN_SYNC_NAMESPACE = uuid5(NAMESPACE_URL, "prism-agent:agent-run-sync")
_TARGET_TYPES_BY_ID_FIELD = {
    "itemId": "work_item",
    "workItemId": "work_item",
    "sprintId": "sprint",
}


def resolve_agent_run_id(context: AgentContext) -> str:
    """Resolve the stable Prism agent run id used as plan_id.

    The API owns run identity. The runtime must reuse the same run id across
    retries and recursion instead of minting a new value on every planning pass.
    """
    event = context.source_event.event
    payload = event.payload

    for key in _AGENT_RUN_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    queue_pointer = payload.get("queue_pointer")
    if isinstance(queue_pointer, dict):
        for key in _AGENT_RUN_ID_KEYS:
            value = queue_pointer.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if event.correlation_id:
        return event.correlation_id

    if event.idempotency_key:
        return event.idempotency_key

    return event.event_id


def action_status_to_api(status: ActionStatus, *, requires_approval: bool) -> str:
    if status == ActionStatus.COMPLETED:
        return "executed"
    if status == ActionStatus.FAILED:
        return "failed"
    if status in {ActionStatus.SKIPPED, ActionStatus.STALE}:
        return "cancelled"
    if status == ActionStatus.RUNNING:
        return "approved"
    if status == ActionStatus.REQUIRES_APPROVAL or requires_approval:
        return "proposed"
    return "approved"


def step_status_to_api(status: ActionStatus) -> str:
    if status == ActionStatus.COMPLETED:
        return "completed"
    if status in {ActionStatus.FAILED, ActionStatus.STALE}:
        return "failed"
    if status == ActionStatus.SKIPPED:
        return "skipped"
    if status == ActionStatus.RUNNING:
        return "running"
    return "pending"


def run_status_to_api(plan: AgentPlan) -> str:
    if plan.status.value == "completed":
        return "completed"
    if plan.status.value == "failed":
        return "failed"
    if plan.status.value == "cancelled":
        return "cancelled"
    if plan.status.value in {"waiting_for_approval", "replan_required"}:
        return "waiting"
    if any(action.status == ActionStatus.RUNNING for action in plan.actions):
        return "running"
    return "running"


def action_step_order(plan: AgentPlan, action: PlannedAction) -> int:
    for index, candidate in enumerate(plan.actions, start=1):
        if candidate.action_id == action.action_id:
            return index
    raise ValueError(f"Action {action.action_id} is not part of plan {plan.plan_id}.")


def action_step_title(action: PlannedAction) -> str:
    return " ".join((action.instruction.strip() or action.tool_name).split())


def stable_agent_run_uuid(*parts: str) -> str:
    raw = ":".join(parts)
    try:
        return str(UUID(raw))
    except ValueError:
        return str(uuid5(_AGENT_RUN_SYNC_NAMESPACE, raw))


def action_api_id(action: PlannedAction) -> str:
    return stable_agent_run_uuid(action.plan_id, action.action_id)


def action_api_type(action: PlannedAction) -> str:
    return (action.tool_name or action.action_type or "agent_action")[:50]


def action_target_type(action: PlannedAction, target_id: str | None = None) -> str:
    if target_id is None:
        return "tool"

    explicit = _first_string(action.input, "targetType", "target_type")
    if explicit:
        return explicit[:50]

    target_ref = _first_string(action.input, "target_entity_ref", "targetEntityRef")
    if target_ref and ":" in target_ref and _uuid_string(target_ref.split(":", 1)[1]):
        return target_ref.split(":", 1)[0][:50]

    for key, target_type in _TARGET_TYPES_BY_ID_FIELD.items():
        if _uuid_string(_first_string(action.input, key)):
            return target_type

    return "tool"


def action_target_id(action: PlannedAction) -> str | None:
    value = _first_string(action.input, "targetId", "target_id", "itemId", "workItemId", "sprintId")
    if value is not None:
        return _uuid_string(value)

    target_ref = _first_string(action.input, "target_entity_ref", "targetEntityRef")
    if target_ref and ":" in target_ref:
        return _uuid_string(target_ref.split(":", 1)[1])
    return None


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _uuid_string(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None
