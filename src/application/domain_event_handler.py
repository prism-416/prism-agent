from __future__ import annotations

from application.context_provider import ContextProvider
from application.event_router import EventRouter
from application.planner import Planner
from domain.events import AgentActionEvent, EventEnvelope
from domain.results import TraceEvent
from infrastructure.queue.base import Queue
from infrastructure.state.base import StateStore


class DomainEventHandler:
    def __init__(
        self,
        router: EventRouter,
        context_provider: ContextProvider,
        planner: Planner,
        state_store: StateStore,
        queue: Queue,
    ) -> None:
        self.router = router
        self.context_provider = context_provider
        self.planner = planner
        self.state_store = state_store
        self.queue = queue

    def handle(self, envelope: EventEnvelope) -> None:
        route = self.router.route(envelope)
        if not route.decision.allowed or route.workflow is None:
            self._trace(
                envelope,
                "event.ignored",
                f"Ignored event: {route.decision.reason}",
                {"trigger_key": route.decision.trigger_key},
            )
            return

        snapshot = self.context_provider.hydrate(envelope, route.workflow)
        plan = self.planner.create_plan(snapshot.context, snapshot.ref, route.workflow)
        snapshot = snapshot.model_copy(update={"plan_id": plan.plan_id})
        self.state_store.save_context_snapshot(snapshot)
        self.state_store.save_plan(plan)
        self._trace(
            envelope,
            "plan.created",
            f"Created plan {plan.plan_id} for workflow {route.workflow.workflow_id}",
            {"action_count": len(plan.actions), "prompt_id": plan.prompt_id},
            plan_id=plan.plan_id,
        )

        first_action = plan.next_pending_action()
        if first_action is None:
            self._trace(
                envelope,
                "plan.empty",
                "Planner produced no executable actions.",
                plan_id=plan.plan_id,
            )
            return

        action_event = AgentActionEvent(
            workspace_id=plan.workspace_id,
            project_id=plan.project_id,
            plan_id=plan.plan_id,
            action_id=first_action.action_id,
            correlation_id=envelope.event.correlation_id,
            causality=envelope.event.causality.child(envelope.event_id),
        )
        self.queue.enqueue(EventEnvelope.wrap(action_event))
        self._trace(
            envelope,
            "action.enqueued",
            f"Enqueued first action {first_action.action_id}",
            {"tool_name": first_action.tool_name},
            plan_id=plan.plan_id,
            action_id=first_action.action_id,
        )

    def _trace(
        self,
        envelope: EventEnvelope,
        event_name: str,
        message: str,
        data: dict | None = None,
        plan_id: str | None = None,
        action_id: str | None = None,
    ) -> None:
        self.state_store.append_trace_event(
            TraceEvent(
                event_name=event_name,
                workspace_id=envelope.event.workspace_id,
                project_id=envelope.event.project_id,
                plan_id=plan_id,
                action_id=action_id,
                message=message,
                data=data or {},
            )
        )
