from __future__ import annotations

from application.action_event_handler import ActionEventHandler
from application.agent_run_sync import AgentRunSync
from application.domain_event_handler import DomainEventHandler
from domain.events import AgentActionEvent, EventEnvelope, RuntimeEvent
from domain.results import TraceEvent
from infrastructure.queue.base import Queue
from infrastructure.state.base import StateStore


class RecursionRunner:
    def __init__(
        self,
        queue: Queue,
        state_store: StateStore,
        domain_event_handler: DomainEventHandler,
        action_event_handler: ActionEventHandler,
        max_recursion_depth: int,
        agent_run_sync: AgentRunSync | None = None,
    ) -> None:
        self.queue = queue
        self.state_store = state_store
        self.domain_event_handler = domain_event_handler
        self.action_event_handler = action_event_handler
        self.max_recursion_depth = max_recursion_depth
        self.agent_run_sync = agent_run_sync
        self._seen_event_ids: set[str] = set()

    def run(self, seed_event: RuntimeEvent | EventEnvelope) -> list[TraceEvent]:
        envelope = (
            seed_event if isinstance(seed_event, EventEnvelope) else EventEnvelope.wrap(seed_event)
        )
        self.queue.enqueue(envelope)
        processed = 0

        while not self.queue.is_empty():
            message = self.queue.dequeue()
            if message is None:
                break
            event = message.envelope.event

            if (
                processed >= self.max_recursion_depth
                or event.causality.depth > self.max_recursion_depth
            ):
                self.queue.fail(message, "max_recursion_depth_exceeded")
                self.record_max_depth_failure(event)
                continue

            if event.event_id in self._seen_event_ids:
                self.queue.ack(message)
                self.state_store.append_trace_event(
                    TraceEvent(
                        event_name="event.duplicate",
                        workspace_id=event.workspace_id,
                        project_id=event.project_id,
                        message="Skipped duplicate event.",
                        data={"event_id": event.event_id},
                    )
                )
                continue

            try:
                self._seen_event_ids.add(event.event_id)
                if isinstance(event, AgentActionEvent):
                    self.action_event_handler.handle(message.envelope)
                else:
                    self.domain_event_handler.handle(message.envelope)
                self.queue.ack(message)
            except Exception as exc:
                self.queue.fail(message, str(exc))
                self.state_store.append_trace_event(
                    TraceEvent(
                        event_name="event.failed",
                        workspace_id=event.workspace_id,
                        project_id=event.project_id,
                        message=str(exc),
                        data={"event_id": event.event_id, "event_type": event.event_type},
                    )
                )
            finally:
                processed += 1

        traces = getattr(self.state_store, "traces", None)
        return list(traces) if traces is not None else []

    def exceeds_max_depth(self, envelope: EventEnvelope, *, processed: int = 0) -> bool:
        event = envelope.event
        return (
            processed >= self.max_recursion_depth
            or event.causality.depth > self.max_recursion_depth
        )

    def record_max_depth_failure(self, event: RuntimeEvent) -> None:
        message = "Stopped recursive processing at max depth."
        self.state_store.append_trace_event(
            TraceEvent(
                event_name="recursion.max_depth",
                workspace_id=event.workspace_id,
                project_id=event.project_id,
                message=message,
                data={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "depth": event.causality.depth,
                },
            )
        )
        run_id = _event_run_id(event)
        if self.agent_run_sync is not None and run_id is not None:
            self.agent_run_sync.record_run_failed(event.workspace_id, run_id, message)


def _event_run_id(event: RuntimeEvent) -> str | None:
    if isinstance(event, AgentActionEvent):
        return event.plan_id
    for key in ("agentRunId", "runId", "agent_run_id"):
        value = event.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    queue_pointer = event.payload.get("queue_pointer")
    if isinstance(queue_pointer, dict):
        for key in ("agentRunId", "runId", "agent_run_id"):
            value = queue_pointer.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if event.correlation_id:
        return event.correlation_id
    return event.idempotency_key
