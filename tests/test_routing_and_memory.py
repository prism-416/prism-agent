from application.action_event_handler import ActionEventHandler
from application.event_router import EventRouter
from application.trigger_policy import TriggerPolicy
from domain.actions import ActionStatus, PlannedAction
from domain.context import AgentContext, ContextSnapshot
from domain.events import AgentActionEvent, DomainEvent, EventEnvelope
from domain.plans import AgentPlan
from infrastructure.queue.memory_queue import MemoryQueue
from infrastructure.registries.workflow_registry import WorkflowRegistry
from infrastructure.state.memory_state_store import MemoryStateStore
from infrastructure.state.prism_api_state_store import PrismApiStateStore


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


def test_prism_api_state_store_hydrates_plan_actions_on_each_read() -> None:
    prism_client = _FakeAgentStatePrismClient()
    store = PrismApiStateStore(prism_client, MemoryStateStore())
    plan = AgentPlan(
        source_event_id="e1",
        workspace_id="w1",
        project_id="p1",
        goal="test",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx1",
    )
    first_action = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        input={"title": "Task"},
        idempotency_key="k1",
    )
    second_action = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="update_workitem_status",
        instruction="Mark the task done.",
        input={"status": "done"},
        depends_on=[first_action.action_id],
        idempotency_key="k2",
    )
    plan = plan.model_copy(update={"actions": [first_action, second_action]})
    prism_client.records = [
        {
            "actionId": "missing-action",
            "status": "completed",
            "requiresApproval": False,
        },
        {
            "actionId": first_action.action_id,
            "status": "completed",
            "requiresApproval": False,
        },
        {
            "actionId": second_action.action_id,
            "status": "waiting",
            "requiresApproval": True,
        },
    ]

    store.save_plan(plan)

    hydrated = store.get_plan("w1", plan.plan_id)
    hydrated_again = store.get_plan("w1", plan.plan_id)

    assert hydrated is not None
    assert hydrated_again is not None
    assert prism_client.calls == [("w1", plan.plan_id), ("w1", plan.plan_id)]
    assert hydrated.get_action(first_action.action_id).status == ActionStatus.COMPLETED
    assert hydrated.get_action(second_action.action_id).status == ActionStatus.REQUIRES_APPROVAL


def test_action_handler_skips_hydrated_completed_action_and_enqueues_next() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    executor = _FailingExecutor()
    handler = ActionEventHandler(executor, _FailingValidator(), store, queue)
    source_event = EventEnvelope.wrap(
        DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
    )
    snapshot = ContextSnapshot(
        workspace_id="w1",
        project_id="p1",
        workflow_id="story.decompose",
        context=AgentContext(
            workspace_id="w1",
            project_id="p1",
            source_event=source_event,
            workflow_id="story.decompose",
        ),
    )
    plan = AgentPlan(
        source_event_id=source_event.event_id,
        workspace_id="w1",
        project_id="p1",
        goal="test",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref=snapshot.ref,
    )
    first_action = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        idempotency_key="k1",
        status=ActionStatus.COMPLETED,
    )
    second_action = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="update_workitem_status",
        instruction="Mark the task done.",
        depends_on=[first_action.action_id],
        idempotency_key="k2",
    )
    plan = plan.model_copy(update={"actions": [first_action, second_action]})
    store.save_context_snapshot(snapshot)
    store.save_plan(plan)

    handler.handle(
        EventEnvelope.wrap(
            AgentActionEvent(
                workspace_id="w1",
                project_id="p1",
                plan_id=plan.plan_id,
                action_id=first_action.action_id,
            )
        )
    )

    message = queue.dequeue()
    assert executor.called is False
    assert message is not None
    assert isinstance(message.envelope.event, AgentActionEvent)
    assert message.envelope.event.action_id == second_action.action_id
    assert [trace.event_name for trace in store.traces] == [
        "action.not_pending",
        "action.enqueued",
    ]


class _FakeAgentStatePrismClient:
    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.records: list[dict] = []

    def get_agent_run_actions(self, workspace_id: str, run_id: str) -> list[dict]:
        self.calls.append((workspace_id, run_id))
        return self.records


class _FailingExecutor:
    def __init__(self) -> None:
        self.called = False

    def execute_one(self, *args, **kwargs):
        self.called = True
        raise AssertionError("executor should not be called")


class _FailingValidator:
    def validate_before_execution(self, *args, **kwargs):
        raise AssertionError("validator should not be called")
