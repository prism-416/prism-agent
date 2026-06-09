from __future__ import annotations

from typing import Any
from uuid import UUID

from domain.actions import PlannedAction
from domain.agent_run import (
    action_api_id,
    action_api_type,
    action_status_to_api,
    action_step_order,
    action_step_title,
    action_target_id,
    action_target_type,
    run_status_to_api,
    stable_agent_run_uuid,
    step_status_to_api,
)
from domain.context import AgentContext
from domain.plans import AgentPlan
from infrastructure.prism_api.client import PrismApiClient


class AgentRunSync:
    def __init__(self, prism_client: PrismApiClient, *, enabled: bool) -> None:
        self.prism_client = prism_client
        self.enabled = enabled and prism_client.is_configured

    def record_run_started(
        self,
        context: AgentContext,
        run_id: str,
        *,
        objective: str,
        prompt_version: str,
    ) -> None:
        if not self.enabled:
            return
        self.prism_client.create_agent_run(
            context.workspace_id,
            _agent_run_create_payload(
                run_id=run_id,
                context=context,
                objective=objective,
                prompt_version=prompt_version,
            ),
        )

    def record_plan_created(self, plan: AgentPlan, context: AgentContext | None = None) -> None:
        if not self.enabled:
            return
        _ = context
        self.prism_client.upsert_agent_run_step(
            plan.workspace_id,
            _api_run_id(plan),
            {
                "stepId": stable_agent_run_uuid(plan.plan_id, "plan"),
                "stepOrder": 0,
                "stepType": "plan",
                "status": "completed",
                "title": (plan.goal.strip() or "Plan")[:100],
                "outputSummary": f"Planned {len(plan.actions)} action(s).",
            },
        )
        for action in plan.actions:
            self._upsert_action(plan, action, include_step_id=False)

    def record_run_failed(self, workspace_id: str, run_id: str, message: str) -> None:
        if not self.enabled:
            return
        self.prism_client.upsert_agent_run_step(
            workspace_id,
            run_id,
            {
                "stepId": stable_agent_run_uuid(run_id, "plan"),
                "stepOrder": 0,
                "stepType": "plan",
                "status": "failed",
                "title": "Plan",
                "outputSummary": "Planning failed.",
                "errorMessage": message[:5000],
            },
        )
        self.prism_client.update_agent_run_status(
            workspace_id,
            run_id,
            {"status": "failed"},
        )

    def record_run_running(self, workspace_id: str, run_id: str) -> None:
        if not self.enabled:
            return
        self.prism_client.update_agent_run_status(
            workspace_id,
            run_id,
            {"status": "running"},
        )

    def record_action_state(
        self,
        plan: AgentPlan,
        action: PlannedAction,
        *,
        event_name: str,
        message: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        action_id = action_api_id(action)
        step_id = action_id
        step_payload = {
            "stepId": step_id,
            "stepOrder": action_step_order(plan, action),
            "stepType": "execute",
            "status": step_status_to_api(action.status),
            "title": action_step_title(action)[:100],
            "inputSummary": action.instruction[:5000],
            "outputSummary": (message or event_name)[:5000],
        }
        if step_payload["status"] == "failed" and message:
            step_payload["errorMessage"] = message[:5000]
        self.prism_client.upsert_agent_run_step(
            plan.workspace_id,
            _api_run_id(plan),
            step_payload,
        )
        self._upsert_action(
            plan,
            action,
            include_step_id=True,
            message=message,
        )
        if message:
            self.prism_client.create_agent_action_event(
                plan.workspace_id,
                action_id,
                {
                    "eventType": event_name,
                    "message": message[:5000],
                },
            )
        # A sub-plan never finalizes the shared run; the orchestration coordinator
        # owns run completion once every node (and the synthesizer) is done.
        run_status = "running" if plan.is_subplan else run_status_to_api(plan)
        self.prism_client.update_agent_run_status(
            plan.workspace_id,
            _api_run_id(plan),
            {"status": run_status},
        )

    def record_run_completed(self, plan: AgentPlan) -> None:
        if not self.enabled:
            return
        self.record_run_completed_by_id(plan.workspace_id, _api_run_id(plan))

    def record_run_completed_by_id(self, workspace_id: str, run_id: str) -> None:
        if not self.enabled:
            return
        self.prism_client.update_agent_run_status(
            workspace_id,
            run_id,
            {"status": "completed"},
        )

    def _upsert_action(
        self,
        plan: AgentPlan,
        action: PlannedAction,
        *,
        include_step_id: bool,
        message: str | None = None,
    ) -> None:
        target_id = action_target_id(action)
        payload = {
            "actionId": action_api_id(action),
            "actionType": action_api_type(action),
            "targetType": action_target_type(action, target_id),
            "status": action_status_to_api(
                action.status,
                requires_approval=action.requires_approval,
            ),
            "reasoningSummary": action.instruction[:5000],
            "requiresApproval": action.requires_approval,
        }
        if include_step_id:
            payload["stepId"] = action_api_id(action)
        if target_id:
            payload["targetId"] = target_id
        if payload["status"] in {"failed", "cancelled"} and message:
            payload["errorMessage"] = message[:5000]

        self.prism_client.upsert_agent_action(
            plan.workspace_id,
            _api_run_id(plan),
            payload,
        )


def _api_run_id(plan: AgentPlan) -> str:
    """The Prism agent-run id for a plan. Sub-plans report under their parent run."""
    return plan.parent_run_id or plan.plan_id


def _agent_run_create_payload(
    *,
    run_id: str,
    context: AgentContext,
    objective: str,
    prompt_version: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agentType": "project_manager",
        "objective": (objective.strip() or context.workflow_id)[:5000],
        "runId": run_id,
        "systemPromptVersion": prompt_version[:100],
        "triggerType": _trigger_type(context),
        "status": "running",
    }
    triggered_by_user_id = _triggered_by_user_id(context)
    if triggered_by_user_id:
        payload["triggeredByUserId"] = triggered_by_user_id

    work_item_id = _work_item_id(context)
    if work_item_id:
        payload["workItemId"] = work_item_id

    return payload


def _trigger_type(context: AgentContext | None) -> str:
    if context is None:
        return "event"
    event = context.source_event.event
    if event.kind == "manual":
        return "manual"
    if event.kind == "scheduled":
        return "scheduled"
    if event.kind == "agent_action":
        return "recursive"
    return "event"


def _triggered_by_user_id(context: AgentContext) -> str | None:
    event = context.source_event.event
    actor_id = getattr(event, "actor_id", None)
    if isinstance(actor_id, str):
        user_id = _uuid_string(actor_id)
        if user_id:
            return user_id

    payload = event.payload
    user_id = _uuid_string(
        _first_string(payload, "triggeredByUserId", "requestedByUserId", "userId")
    )
    if user_id:
        return user_id

    queue_pointer = payload.get("queue_pointer")
    if isinstance(queue_pointer, dict):
        return _uuid_string(
            _first_string(queue_pointer, "triggeredByUserId", "requestedByUserId", "userId")
        )
    return None


def _work_item_id(context: AgentContext) -> str | None:
    payload = context.source_event.event.payload
    work_item_id = _uuid_string(_first_string(payload, "workItemId", "work_item_id", "itemId"))
    if work_item_id:
        return work_item_id

    queue_pointer = payload.get("queue_pointer")
    if isinstance(queue_pointer, dict):
        return _uuid_string(_first_string(queue_pointer, "workItemId", "work_item_id", "itemId"))
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
