import pytest

from application.action_event_handler import ActionEventHandler
from application.agent_run_sync import AgentRunSync
from application.event_router import EventRouter
from application.trigger_policy import TriggerPolicy
from domain.actions import ActionStatus, PlannedAction
from domain.agent_run import action_api_id
from domain.context import AgentContext, ContextSnapshot
from domain.events import AgentActionEvent, DomainEvent, EventEnvelope
from domain.plans import AgentPlan, PlanStatus
from domain.results import ToolResult, ValidationDecision, ValidationResult
from infrastructure.prism_api.client import PrismApiClient
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


def test_event_router_routes_pull_request_review_triggers() -> None:
    router = EventRouter(TriggerPolicy(), WorkflowRegistry.with_defaults())
    for event_type in ("pr.opened", "pr.synchronize", "pr.review_requested"):
        route = router.route(
            EventEnvelope.wrap(
                DomainEvent(event_type=event_type, workspace_id="w1", project_id="p1")
            )
        )
        assert route.decision.allowed is True, event_type
        assert route.workflow is not None, event_type
        assert route.workflow.workflow_id == "pr.review", event_type


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
        action_id="create-task-action",
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
            "status": "executed",
            "requiresApproval": False,
        },
        {
            "actionId": action_api_id(first_action),
            "status": "executed",
            "requiresApproval": False,
        },
        {
            "actionId": second_action.action_id,
            "status": "proposed",
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


def test_prism_api_state_store_skips_client_scoped_memory_writes_by_default() -> None:
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

    store.save_plan(plan)

    assert prism_client.memories == []


def test_prism_api_state_store_restores_plan_context_result_and_idempotency() -> None:
    prism_client = _FakeAgentStatePrismClient()
    first_store = PrismApiStateStore(
        prism_client,
        MemoryStateStore(),
        persist_agent_memories=True,
    )
    source_event = EventEnvelope.wrap(
        DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
    )
    context = AgentContext(
        workspace_id="w1",
        project_id="p1",
        source_event=source_event,
        workflow_id="story.decompose",
    )
    snapshot = ContextSnapshot(
        plan_id="run-1",
        workspace_id="w1",
        project_id="p1",
        workflow_id="story.decompose",
        context=context,
    )
    action = PlannedAction(
        action_id="create-task-action",
        plan_id="run-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        input={"title": "Task"},
        idempotency_key="k1",
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id=source_event.event_id,
        workspace_id="w1",
        project_id="p1",
        goal="test",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref=snapshot.ref,
        actions=[action],
    )
    result = ToolResult(
        plan_id=plan.plan_id,
        action_id=action.action_id,
        tool_name=action.tool_name,
        success=True,
        output={"itemId": "item-1"},
    )

    first_store.save_context_snapshot(snapshot)
    first_store.save_plan(plan)
    first_store.save_action_result(result)
    first_store.record_idempotency_key(action.idempotency_key)

    restored_store = PrismApiStateStore(prism_client, MemoryStateStore())
    restored_plan = restored_store.get_plan("w1", plan.plan_id)
    restored_snapshot = restored_store.get_context_snapshot("w1", snapshot.ref)
    restored_result = restored_store.get_action_result(plan.plan_id, action.action_id)

    assert restored_plan == plan
    assert restored_snapshot == snapshot
    assert restored_result == result
    assert restored_store.check_idempotency_key(action.idempotency_key) is True


def test_prism_api_state_store_hydrates_cancelled_run_status() -> None:
    prism_client = _FakeAgentStatePrismClient()
    prism_client.run_status = "cancelled"
    store = PrismApiStateStore(prism_client, MemoryStateStore())
    action = PlannedAction(
        action_id="create-task-action",
        plan_id="run-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        input={"title": "Task"},
        idempotency_key="k1",
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id="e1",
        workspace_id="w1",
        project_id="p1",
        goal="test",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx1",
        actions=[action],
    )

    store.save_plan(plan)
    hydrated = store.get_plan("w1", plan.plan_id)

    assert hydrated is not None
    assert hydrated.status == PlanStatus.CANCELLED
    assert hydrated.next_pending_action() is None


def test_action_handler_skips_hydrated_completed_action_and_enqueues_next() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    executor = _FailingExecutor()
    handler = ActionEventHandler(
        executor,
        _FailingValidator(),
        store,
        queue,
        AgentRunSync(PrismApiClient(), enabled=False),
    )
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
        requires_approval=True,
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

    # Approval-gated work still crosses the queue; auto-commit work is chained
    # in the same invocation (covered separately below).
    message = queue.dequeue()
    assert executor.called is False
    assert message is not None
    assert isinstance(message.envelope.event, AgentActionEvent)
    assert message.envelope.event.action_id == second_action.action_id
    assert [trace.event_name for trace in store.traces] == [
        "action.not_pending",
        "action.enqueued",
    ]


def test_action_handler_marks_failed_state_when_executor_raises() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    agent_run_sync = _CapturingAgentRunSync()
    handler = ActionEventHandler(
        _RaisingExecutor(RuntimeError("assignee not found")),
        _CommitValidator(),
        store,
        queue,
        agent_run_sync,
    )
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
    action = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        idempotency_key="k1",
    )
    plan = plan.model_copy(update={"actions": [action]})
    store.save_context_snapshot(snapshot)
    store.save_plan(plan)

    with pytest.raises(RuntimeError, match="assignee not found"):
        handler.handle(
            EventEnvelope.wrap(
                AgentActionEvent(
                    workspace_id="w1",
                    project_id="p1",
                    plan_id=plan.plan_id,
                    action_id=action.action_id,
                )
            )
        )

    updated_plan = store.get_plan("w1", plan.plan_id)
    assert updated_plan.status.value == "failed"
    assert updated_plan.get_action(action.action_id).status == ActionStatus.FAILED
    assert [call[2] for call in agent_run_sync.calls] == [
        "action.executing",
        "action.execution_error",
    ]
    failure_plan, failure_action, _, failure_message = agent_run_sync.calls[-1]
    assert failure_plan.status.value == "failed"
    assert failure_action.status == ActionStatus.FAILED
    assert failure_message == "Tool create_workitem failed: assignee not found"
    assert [trace.event_name for trace in store.traces][-2:] == [
        "action.execution_error",
        "action.failed",
    ]


def test_action_handler_retries_failed_tool_result_before_failing() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    handler = ActionEventHandler(
        _ResultExecutor(success=False, error="temporary failure"),
        _CommitThenResultValidator(ValidationDecision.RETRY),
        store,
        queue,
        _CapturingAgentRunSync(),
    )
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
    action = PlannedAction(
        action_id="action-1",
        plan_id="run-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        idempotency_key="k1",
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id=source_event.event_id,
        workspace_id="w1",
        project_id="p1",
        goal="test",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref=snapshot.ref,
        actions=[action],
    )
    store.save_context_snapshot(snapshot)
    store.save_plan(plan)

    handler.handle(
        EventEnvelope.wrap(
            AgentActionEvent(
                workspace_id="w1",
                project_id="p1",
                plan_id=plan.plan_id,
                action_id=action.action_id,
            )
        )
    )

    retry_message = queue.dequeue()
    updated_plan = store.get_plan("w1", plan.plan_id)
    assert retry_message is not None
    assert retry_message.envelope.attempt == 1
    assert isinstance(retry_message.envelope.event, AgentActionEvent)
    assert retry_message.envelope.event.action_id == action.action_id
    assert updated_plan is not None
    assert updated_plan.get_action(action.action_id).status == ActionStatus.PENDING
    assert store.traces[-1].event_name == "action.retry_scheduled"


def test_action_handler_enqueues_replan_on_stale_context() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    handler = ActionEventHandler(
        _FailingExecutor(),
        _StaleValidator(),
        store,
        queue,
        _CapturingAgentRunSync(),
    )
    source_event = EventEnvelope.wrap(
        DomainEvent(
            event_type="story.created",
            workspace_id="w1",
            project_id="p1",
            payload={"story": {"id": "story-1"}, "entity_versions": {"story:story-1": 1}},
            correlation_id="run-1",
        )
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
    action = PlannedAction(
        action_id="action-1",
        plan_id="run-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        idempotency_key="k1",
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id=source_event.event_id,
        workspace_id="w1",
        project_id="p1",
        goal="test",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref=snapshot.ref,
        actions=[action],
    )
    store.save_context_snapshot(snapshot)
    store.save_plan(plan)

    handler.handle(
        EventEnvelope.wrap(
            AgentActionEvent(
                workspace_id="w1",
                project_id="p1",
                plan_id=plan.plan_id,
                action_id=action.action_id,
            )
        )
    )

    replan_message = queue.dequeue()
    updated_plan = store.get_plan("w1", plan.plan_id)
    assert replan_message is not None
    assert isinstance(replan_message.envelope.event, DomainEvent)
    assert replan_message.envelope.event.payload["agentRunId"] == plan.plan_id
    assert "_agent_replan" in replan_message.envelope.event.payload
    assert "entity_versions" not in replan_message.envelope.event.payload
    assert updated_plan is not None
    assert updated_plan.status == PlanStatus.REPLAN_REQUIRED
    assert updated_plan.get_action(action.action_id).status == ActionStatus.STALE
    assert store.traces[-1].event_name == "plan.replan_enqueued"


class _FakeAgentStatePrismClient:
    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.records: list[dict] = []
        self.memories: list[dict] = []
        self.run_status = "running"

    def get_agent_run_state(self, workspace_id: str, run_id: str) -> dict:
        self.calls.append((workspace_id, run_id))
        return {
            "run": {"runId": run_id, "workspaceId": workspace_id, "status": self.run_status},
            "steps": [],
            "actions": self.records,
            "actionEvents": [],
            "memories": self.memories,
        }

    def upsert_agent_memory(self, workspace_id: str, payload: dict) -> dict:
        _ = workspace_id
        content = payload.get("content")
        for index, memory in enumerate(self.memories):
            if memory.get("memoryId") == payload.get("memoryId"):
                self.memories[index] = {**payload, "content": content}
                break
        else:
            self.memories.append({**payload, "content": content})
        return {"memoryId": payload.get("memoryId")}


class _FailingExecutor:
    def __init__(self) -> None:
        self.called = False

    def execute_one(self, *args, **kwargs):
        self.called = True
        raise AssertionError("executor should not be called")


class _FailingValidator:
    def validate_before_execution(self, *args, **kwargs):
        raise AssertionError("validator should not be called")


class _CommitValidator:
    def validate_before_execution(self, *args, **kwargs):
        _ = (args, kwargs)
        return ValidationResult(decision=ValidationDecision.COMMIT, valid=True)

    def validate_after_execution(self, *args, **kwargs):
        _ = (args, kwargs)
        return ValidationResult(decision=ValidationDecision.COMMIT, valid=True)


class _CommitThenResultValidator:
    def __init__(self, post_decision: ValidationDecision) -> None:
        self.post_decision = post_decision

    def validate_before_execution(self, *args, **kwargs):
        _ = (args, kwargs)
        return ValidationResult(decision=ValidationDecision.COMMIT, valid=True)

    def validate_after_execution(self, *args, **kwargs):
        _ = (args, kwargs)
        return ValidationResult(
            decision=self.post_decision,
            valid=self.post_decision == ValidationDecision.COMMIT,
            reason="temporary failure",
        )


class _StaleValidator:
    def validate_before_execution(self, *args, **kwargs):
        _ = (args, kwargs)
        return ValidationResult(
            decision=ValidationDecision.REPLAN,
            valid=False,
            reason="stale_context",
            stale_entities={"story:story-1": {"expected": 1, "actual": 2}},
        )


class _ResultExecutor:
    def __init__(self, *, success: bool, error: str | None = None) -> None:
        self.success = success
        self.error = error

    def execute_one(self, action, *args, **kwargs):
        _ = (args, kwargs)
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=action.tool_name,
            success=self.success,
            error=self.error,
        )


class _RaisingExecutor:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def execute_one(self, *args, **kwargs):
        _ = (args, kwargs)
        raise self.exc


class _CapturingAgentRunSync:
    def __init__(self) -> None:
        self.calls = []

    def record_action_state(self, plan, action, *, event_name, message=None) -> None:
        self.calls.append((plan, action, event_name, message))


def test_auto_commit_actions_chain_within_one_invocation() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    handler = ActionEventHandler(
        _ResultExecutor(success=True),
        _CommitValidator(),
        store,
        queue,
        AgentRunSync(PrismApiClient(), enabled=False),
    )
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
    first = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="create_sprint",
        instruction="Create sprint.",
        idempotency_key="k1",
    )
    second = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="create_workitem_tree",
        instruction="Create breakdown.",
        depends_on=[first.action_id],
        idempotency_key="k2",
    )
    plan = plan.model_copy(update={"actions": [first, second]})
    store.save_context_snapshot(snapshot)
    store.save_plan(plan)

    handler.handle(
        EventEnvelope.wrap(
            AgentActionEvent(
                workspace_id="w1",
                project_id="p1",
                plan_id=plan.plan_id,
                action_id=first.action_id,
            )
        )
    )

    # Both actions completed in one invocation; nothing crossed the queue.
    assert queue.dequeue() is None
    final_plan = store.get_plan("w1", plan.plan_id)
    assert final_plan.status.value == "completed"
    trace_names = [trace.event_name for trace in store.traces]
    assert "action.chained" in trace_names
    assert "plan.completed" in trace_names
    assert trace_names.count("action.completed") == 2
