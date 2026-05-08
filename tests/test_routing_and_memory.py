from application.event_router import EventRouter
from application.trigger_policy import TriggerPolicy
from domain.events import DomainEvent, EventEnvelope
from domain.plans import AgentPlan
from infrastructure.queue.memory_queue import MemoryQueue
from infrastructure.registries.workflow_registry import WorkflowRegistry
from infrastructure.state.memory_state_store import MemoryStateStore


def test_event_router_routes_allowed_workflow() -> None:
    router = EventRouter(TriggerPolicy(), WorkflowRegistry.with_defaults())
    route = router.route(
        EventEnvelope.wrap(DomainEvent(event_type="pr.merged", workspace_id="w1", project_id="p1"))
    )

    assert route.decision.allowed is True
    assert route.workflow is not None
    assert route.workflow.workflow_id == "pr.status_sync"


def test_memory_queue_enqueues_dequeues_and_acks() -> None:
    queue = MemoryQueue()
    envelope = EventEnvelope.wrap(
        DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
    )

    message = queue.enqueue(envelope)
    dequeued = queue.dequeue()
    assert dequeued == message

    queue.ack(message)
    assert queue.is_empty()
    assert message.message_id in queue.acked


def test_memory_state_store_tracks_plans_and_idempotency() -> None:
    store = MemoryStateStore()
    plan = AgentPlan(
        source_event_id="e1",
        workspace_id="w1",
        project_id="p1",
        goal="test",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx1",
    )

    store.save_plan(plan)
    assert store.get_plan("w1", plan.plan_id) == plan

    assert store.check_idempotency_key("k1") is False
    store.record_idempotency_key("k1")
    assert store.check_idempotency_key("k1") is True
