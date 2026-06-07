from __future__ import annotations

from typing import Any

from application.agent_run_sync import AgentRunSync
from application.executor import Executor
from application.trace_data import (
    action_trace_data,
    context_trace_data,
    tool_result_trace_data,
    validation_trace_data,
)
from application.validator import Validator
from domain.actions import ActionStatus
from domain.errors import ActionNotFoundError, PlanNotFoundError
from domain.events import (
    AgentActionEvent,
    DomainEvent,
    EventEnvelope,
    ManualInvocationEvent,
    ScheduledEvent,
)
from domain.plans import PlanStatus
from domain.results import TraceEvent, ValidationDecision
from infrastructure.queue.base import Queue
from infrastructure.state.base import StateStore

ACTION_EXECUTION_ATTEMPTS = 3


class ActionEventHandler:
    def __init__(
        self,
        executor: Executor,
        validator: Validator,
        state_store: StateStore,
        queue: Queue,
        agent_run_sync: AgentRunSync,
    ) -> None:
        self.executor = executor
        self.validator = validator
        self.state_store = state_store
        self.queue = queue
        self.agent_run_sync = agent_run_sync

    def handle(self, envelope: EventEnvelope) -> None:
        event = envelope.event
        if not isinstance(event, AgentActionEvent):
            raise TypeError("ActionEventHandler can only process AgentActionEvent envelopes.")

        plan = self.state_store.get_plan(event.workspace_id, event.plan_id)
        if plan is None:
            raise PlanNotFoundError(event.plan_id)
        action = plan.get_action(event.action_id)
        if action is None:
            raise ActionNotFoundError(event.action_id)

        if action.status != ActionStatus.PENDING:
            self._trace(
                event,
                "action.not_pending",
                "Skipped action because hydrated state is not pending.",
                plan.plan_id,
                action.action_id,
                action_trace_data(action, {"plan_status": plan.status.value}),
            )
            if action.status in {ActionStatus.COMPLETED, ActionStatus.SKIPPED}:
                self._enqueue_next_action(envelope, plan)
            return

        snapshot = self.state_store.get_context_snapshot(
            event.workspace_id, plan.context_snapshot_ref
        )
        if snapshot is None:
            raise PlanNotFoundError(f"context:{plan.context_snapshot_ref}")
        context = snapshot.context

        if self.state_store.check_idempotency_key(action.idempotency_key):
            self._trace(
                event,
                "action.duplicate",
                "Skipped duplicate action execution.",
                plan.plan_id,
                action.action_id,
                action_trace_data(
                    action,
                    {"plan_status": plan.status.value, "duplicate_idempotency_key": True},
                ),
            )
            return

        self._trace(
            event,
            "action.selected",
            f"Selected action {action.action_id} for execution.",
            plan.plan_id,
            action.action_id,
            action_trace_data(
                action,
                {
                    "plan_status": plan.status.value,
                    "context_snapshot_ref": snapshot.ref,
                    "context": context_trace_data(context),
                },
            ),
        )
        self.agent_run_sync.record_action_state(
            plan,
            action,
            event_name="action.selected",
            message=f"Selected action {action.action_id} for execution.",
        )
        pre_validation = self.validator.validate_before_execution(action, context)
        self._trace(
            event,
            "action.pre_validation",
            f"Pre-execution validation decided {pre_validation.decision.value}.",
            plan.plan_id,
            action.action_id,
            action_trace_data(action, {"validation": validation_trace_data(pre_validation)}),
        )
        if pre_validation.decision == ValidationDecision.SUGGEST:
            result = self.validator.approval_result(action, context, pre_validation)
            self.state_store.save_action_result(result)
            updated_action = action.with_status(ActionStatus.REQUIRES_APPROVAL)
            updated_plan = plan.replace_action(updated_action)
            self.state_store.update_plan(updated_plan)
            self.agent_run_sync.record_action_state(
                updated_plan,
                updated_action,
                event_name="action.approval_required",
                message="Action converted to user-facing suggestion.",
            )
            self.state_store.record_idempotency_key(action.idempotency_key)
            self._enqueue_events(pre_validation.emitted_events)
            self._trace(
                event,
                "action.approval_required",
                "Action converted to user-facing suggestion.",
                plan.plan_id,
                action.action_id,
                action_trace_data(
                    updated_action,
                    {
                        "plan_status": updated_plan.status.value,
                        "validation": validation_trace_data(pre_validation),
                        "result": tool_result_trace_data(result),
                    },
                ),
            )
            return

        if pre_validation.decision == ValidationDecision.REPLAN:
            updated_action = action.with_status(ActionStatus.STALE)
            updated_plan = plan.replace_action(updated_action)
            self.state_store.update_plan(updated_plan)
            self.agent_run_sync.record_action_state(
                updated_plan,
                updated_action,
                event_name="action.stale_context",
                message="Action blocked because expected entity versions are stale.",
            )
            self._trace(
                event,
                "action.stale_context",
                "Action blocked because expected entity versions are stale.",
                plan.plan_id,
                action.action_id,
                action_trace_data(
                    updated_action,
                    {
                        "plan_status": updated_plan.status.value,
                        "validation": validation_trace_data(pre_validation),
                    },
                ),
            )
            replan_event = _replan_event(
                context=context,
                plan=updated_plan,
                action=updated_action,
                envelope=envelope,
                stale_entities=pre_validation.stale_entities,
                reason=pre_validation.reason or "stale_context",
            )
            self.queue.enqueue(EventEnvelope.wrap(replan_event))
            self._trace(
                event,
                "plan.replan_enqueued",
                f"Enqueued replan for stale action {action.action_id}.",
                updated_plan.plan_id,
                action.action_id,
                action_trace_data(
                    updated_action,
                    {
                        "plan_status": updated_plan.status.value,
                        "replan_event_type": replan_event.event_type,
                    },
                ),
            )
            return

        if pre_validation.decision == ValidationDecision.FAIL:
            updated_action = action.with_status(ActionStatus.FAILED)
            updated_plan = plan.replace_action(updated_action)
            self.state_store.update_plan(updated_plan)
            self.agent_run_sync.record_action_state(
                updated_plan,
                updated_action,
                event_name="action.validation_failed",
                message=pre_validation.reason or "Action validation failed.",
            )
            self._trace(
                event,
                "action.validation_failed",
                pre_validation.reason or "Action validation failed.",
                plan.plan_id,
                action.action_id,
                action_trace_data(
                    updated_action,
                    {
                        "plan_status": updated_plan.status.value,
                        "validation": validation_trace_data(pre_validation),
                    },
                ),
            )
            return

        running_action = action.with_status(ActionStatus.RUNNING)
        running_plan = plan.replace_action(running_action)
        self.state_store.update_plan(running_plan)
        self.agent_run_sync.record_action_state(
            running_plan,
            running_action,
            event_name="action.executing",
            message=f"Executing tool {action.tool_name} for action {action.action_id}.",
        )
        self._trace(
            event,
            "action.executing",
            f"Executing tool {action.tool_name} for action {action.action_id}.",
            running_plan.plan_id,
            action.action_id,
            action_trace_data(running_action, {"plan_status": running_plan.status.value}),
        )
        try:
            result = self.executor.execute_one(action, context)
        except Exception as exc:
            failed_action = action.with_status(ActionStatus.FAILED)
            failed_plan = running_plan.replace_action(failed_action)
            self.state_store.update_plan(failed_plan)
            failure_message = f"Tool {action.tool_name} failed: {exc}"
            state_sync_error = None
            try:
                self.agent_run_sync.record_action_state(
                    failed_plan,
                    failed_action,
                    event_name="action.execution_error",
                    message=failure_message,
                )
            except Exception as sync_exc:
                state_sync_error = sync_exc
            error_data = {
                "plan_status": failed_plan.status.value,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            if state_sync_error is not None:
                error_data["state_sync_error_type"] = type(state_sync_error).__name__
                error_data["state_sync_error"] = str(state_sync_error)
            self._trace(
                event,
                "action.execution_error",
                f"Tool {action.tool_name} raised before returning a result.",
                failed_plan.plan_id,
                action.action_id,
                action_trace_data(failed_action, error_data),
            )
            self._trace(
                event,
                "action.failed",
                failure_message,
                failed_plan.plan_id,
                action.action_id,
                action_trace_data(failed_action, error_data),
            )
            raise
        self.state_store.save_action_result(result)
        post_validation = self.validator.validate_after_execution(action, context, result)
        self._trace(
            event,
            "action.post_validation",
            f"Post-execution validation decided {post_validation.decision.value}.",
            running_plan.plan_id,
            action.action_id,
            action_trace_data(
                running_action,
                {
                    "plan_status": running_plan.status.value,
                    "validation": validation_trace_data(post_validation),
                    "result": tool_result_trace_data(result),
                },
            ),
        )

        if post_validation.decision == ValidationDecision.COMMIT:
            updated_action = action.with_status(ActionStatus.COMPLETED)
            updated_plan = running_plan.replace_action(updated_action)
            self.state_store.update_plan(updated_plan)
            self.agent_run_sync.record_action_state(
                updated_plan,
                updated_action,
                event_name="action.completed",
                message=f"Completed action {action.action_id}",
            )
            self.state_store.record_idempotency_key(action.idempotency_key)
            self._enqueue_events(post_validation.emitted_events)
            self._trace(
                event,
                "action.completed",
                f"Completed action {action.action_id}",
                updated_plan.plan_id,
                action.action_id,
                action_trace_data(
                    updated_action,
                    {
                        "plan_status": updated_plan.status.value,
                        "result": tool_result_trace_data(result),
                        "validation": validation_trace_data(post_validation),
                    },
                ),
            )
            self._enqueue_next_action(envelope, updated_plan)
            return

        if post_validation.decision == ValidationDecision.RETRY:
            if envelope.attempt + 1 < ACTION_EXECUTION_ATTEMPTS:
                retry_action = action.with_status(ActionStatus.PENDING)
                retry_plan = running_plan.replace_action(retry_action)
                self.state_store.update_plan(retry_plan)
                retry_event = AgentActionEvent(
                    workspace_id=plan.workspace_id,
                    project_id=plan.project_id,
                    plan_id=plan.plan_id,
                    action_id=action.action_id,
                    correlation_id=event.correlation_id,
                    causality=event.causality.child(event.event_id),
                )
                self.queue.enqueue(EventEnvelope(event=retry_event, attempt=envelope.attempt + 1))
                retry_message = post_validation.reason or "Action requested retry."
                self.agent_run_sync.record_action_state(
                    retry_plan,
                    retry_action,
                    event_name="action.retry_scheduled",
                    message=retry_message,
                )
                self._trace(
                    event,
                    "action.retry_scheduled",
                    retry_message,
                    retry_plan.plan_id,
                    action.action_id,
                    action_trace_data(
                        retry_action,
                        {
                            "plan_status": retry_plan.status.value,
                            "attempt": envelope.attempt + 1,
                            "max_attempts": ACTION_EXECUTION_ATTEMPTS,
                            "result": tool_result_trace_data(result),
                            "validation": validation_trace_data(post_validation),
                            "retry_event_id": retry_event.event_id,
                        },
                    ),
                )
                return

        updated_action = action.with_status(ActionStatus.FAILED)
        updated_plan = running_plan.replace_action(updated_action)
        self.state_store.update_plan(updated_plan)
        self.agent_run_sync.record_action_state(
            updated_plan,
            updated_action,
            event_name="action.failed",
            message=post_validation.reason or "Action failed validation after execution.",
        )
        self._trace(
            event,
            "action.failed",
            post_validation.reason or "Action failed validation after execution.",
            updated_plan.plan_id,
            action.action_id,
            action_trace_data(
                updated_action,
                {
                    "plan_status": updated_plan.status.value,
                    "result": tool_result_trace_data(result),
                    "validation": validation_trace_data(post_validation),
                },
            ),
        )

    def _enqueue_next_action(self, envelope: EventEnvelope, plan) -> None:
        next_action = plan.next_pending_action()
        if next_action is None:
            if plan.status == PlanStatus.COMPLETED:
                self.agent_run_sync.record_run_completed(plan)
                event_name = "plan.completed"
                message = f"Plan {plan.plan_id} has no remaining pending actions."
            else:
                event_name = "plan.no_pending_actions"
                message = (
                    f"Plan {plan.plan_id} has no executable actions in status {plan.status.value}."
                )
            self._trace(
                envelope.event,
                event_name,
                message,
                plan.plan_id,
                data={
                    "plan_id": plan.plan_id,
                    "plan_status": plan.status.value,
                    "action_count": len(plan.actions),
                    "completed_action_ids": sorted(plan.completed_action_ids()),
                },
            )
            return
        event = AgentActionEvent(
            workspace_id=plan.workspace_id,
            project_id=plan.project_id,
            plan_id=plan.plan_id,
            action_id=next_action.action_id,
            correlation_id=envelope.event.correlation_id,
            causality=envelope.event.causality.child(envelope.event_id),
        )
        self.queue.enqueue(EventEnvelope.wrap(event))
        self._trace(
            event,
            "action.enqueued",
            f"Enqueued next action {next_action.action_id}",
            plan.plan_id,
            next_action.action_id,
            action_trace_data(
                next_action,
                {
                    "queue_event_id": event.event_id,
                    "queue_event_type": event.event_type,
                },
            ),
        )

    def _enqueue_events(self, envelopes: list[EventEnvelope]) -> None:
        for emitted in envelopes:
            self.queue.enqueue(emitted)

    def _trace(
        self,
        event: AgentActionEvent,
        event_name: str,
        message: str,
        plan_id: str,
        action_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        self.state_store.append_trace_event(
            TraceEvent(
                event_name=event_name,
                workspace_id=event.workspace_id,
                project_id=event.project_id,
                plan_id=plan_id,
                action_id=action_id,
                message=message,
                data=data or {},
            )
        )


def _replan_event(
    *,
    context,
    plan,
    action,
    envelope: EventEnvelope,
    stale_entities: dict[str, dict[str, str | int | None]],
    reason: str,
):
    source = context.source_event.event
    payload = dict(source.payload)
    payload.pop("entity_versions", None)
    payload["agentRunId"] = plan.plan_id
    payload["_agent_replan"] = {
        "reason": reason,
        "blockedActionId": action.action_id,
        "staleEntities": stale_entities,
    }
    common: dict[str, Any] = {
        "event_type": source.event_type,
        "workspace_id": source.workspace_id,
        "project_id": source.project_id,
        "payload": payload,
        "correlation_id": source.correlation_id or envelope.event.correlation_id,
        "causality": envelope.event.causality.child(envelope.event_id),
        "idempotency_key": f"{action.idempotency_key}:replan",
    }
    if isinstance(source, ManualInvocationEvent):
        return ManualInvocationEvent(actor_id=source.actor_id, **common)
    if isinstance(source, ScheduledEvent):
        return ScheduledEvent(schedule_id=source.schedule_id, **common)
    return DomainEvent(**common)
