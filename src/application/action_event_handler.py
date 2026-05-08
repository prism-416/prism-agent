from __future__ import annotations

from application.executor import Executor
from application.validator import Validator
from domain.actions import ActionStatus
from domain.errors import ActionNotFoundError, PlanNotFoundError
from domain.events import AgentActionEvent, EventEnvelope
from domain.results import TraceEvent, ValidationDecision
from infrastructure.queue.base import Queue
from infrastructure.state.base import StateStore


class ActionEventHandler:
    def __init__(
        self,
        executor: Executor,
        validator: Validator,
        state_store: StateStore,
        queue: Queue,
    ) -> None:
        self.executor = executor
        self.validator = validator
        self.state_store = state_store
        self.queue = queue

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
            )
            return

        pre_validation = self.validator.validate_before_execution(action, context)
        if pre_validation.decision == ValidationDecision.SUGGEST:
            result = self.validator.approval_result(action, context, pre_validation)
            self.state_store.save_action_result(result)
            updated_plan = plan.replace_action(action.with_status(ActionStatus.REQUIRES_APPROVAL))
            self.state_store.update_plan(updated_plan)
            self.state_store.record_idempotency_key(action.idempotency_key)
            self._enqueue_events(pre_validation.emitted_events)
            self._trace(
                event,
                "action.approval_required",
                "Action converted to user-facing suggestion.",
                plan.plan_id,
                action.action_id,
                {"tool_name": action.tool_name},
            )
            return

        if pre_validation.decision == ValidationDecision.REPLAN:
            updated_plan = plan.replace_action(action.with_status(ActionStatus.STALE))
            self.state_store.update_plan(updated_plan)
            self._trace(
                event,
                "action.stale_context",
                "Action blocked because expected entity versions are stale.",
                plan.plan_id,
                action.action_id,
                {"stale_entities": pre_validation.stale_entities},
            )
            return

        if pre_validation.decision == ValidationDecision.FAIL:
            updated_plan = plan.replace_action(action.with_status(ActionStatus.FAILED))
            self.state_store.update_plan(updated_plan)
            self._trace(
                event,
                "action.validation_failed",
                pre_validation.reason or "Action validation failed.",
                plan.plan_id,
                action.action_id,
            )
            return

        running_plan = plan.replace_action(action.with_status(ActionStatus.RUNNING))
        self.state_store.update_plan(running_plan)
        result = self.executor.execute_one(action, context)
        self.state_store.save_action_result(result)
        post_validation = self.validator.validate_after_execution(action, context, result)

        if post_validation.decision == ValidationDecision.COMMIT:
            updated_plan = running_plan.replace_action(action.with_status(ActionStatus.COMPLETED))
            self.state_store.update_plan(updated_plan)
            self.state_store.record_idempotency_key(action.idempotency_key)
            self._enqueue_events(post_validation.emitted_events)
            self._trace(
                event,
                "action.completed",
                f"Completed action {action.action_id}",
                updated_plan.plan_id,
                action.action_id,
                {"tool_name": action.tool_name},
            )
            self._enqueue_next_action(envelope, updated_plan)
            return

        updated_plan = running_plan.replace_action(action.with_status(ActionStatus.FAILED))
        self.state_store.update_plan(updated_plan)
        self._trace(
            event,
            "action.failed",
            post_validation.reason or "Action failed validation after execution.",
            updated_plan.plan_id,
            action.action_id,
        )

    def _enqueue_next_action(self, envelope: EventEnvelope, plan) -> None:
        next_action = plan.next_pending_action()
        if next_action is None:
            self._trace(
                envelope.event,
                "plan.completed",
                f"Plan {plan.plan_id} has no remaining pending actions.",
                plan.plan_id,
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
            {"tool_name": next_action.tool_name},
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
