from __future__ import annotations

from uuid import UUID

from application.agent_run_sync import AgentRunSync
from application.domain_event_handler import DomainEventHandler
from application.event_router import RouteResult
from application.orchestrator import Orchestrator
from application.subagent_runner import SubAgentRunner
from domain.actions import ActionStatus, PlannedAction
from domain.agent_run import action_api_id, resolve_agent_run_id, stable_agent_run_uuid
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.plans import AgentPlan, PlanStatus
from domain.policies import TriggerPolicyDecision
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.queue.memory_queue import MemoryQueue
from infrastructure.registries.workflow_registry import WorkflowDefinition
from infrastructure.state.memory_state_store import MemoryStateStore


def test_resolve_agent_run_id_prefers_explicit_pointer_run_id() -> None:
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="w1",
        project_id="p1",
        payload={
            "agentRunId": "run-from-pointer",
            "queue_pointer": {"requestId": "req-1"},
        },
        correlation_id="req-1",
    )
    context = AgentContext(
        workspace_id="w1",
        project_id="p1",
        source_event=EventEnvelope.wrap(event),
        workflow_id="feature.provision",
    )

    assert resolve_agent_run_id(context) == "run-from-pointer"


def test_resolve_agent_run_id_falls_back_to_correlation_id() -> None:
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="w1",
        project_id="p1",
        payload={"queue_pointer": {"requestId": "req-1"}},
        correlation_id="req-1",
    )
    context = AgentContext(
        workspace_id="w1",
        project_id="p1",
        source_event=EventEnvelope.wrap(event),
        workflow_id="feature.provision",
    )

    assert resolve_agent_run_id(context) == "req-1"


def test_agent_run_sync_upserts_plan_and_action_state() -> None:
    prism_client = _CapturingPrismClient()
    sync = AgentRunSync(prism_client, enabled=True)
    source_event = EventEnvelope.wrap(
        DomainEvent(event_type="feature.provisioning.requested", workspace_id="w1", project_id="p1")
    )
    context = AgentContext(
        workspace_id="w1",
        project_id="p1",
        source_event=source_event,
        workflow_id="feature.provision",
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id="event-1",
        workspace_id="w1",
        project_id="p1",
        goal="Provision feature",
        prompt_id="feature.provision",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx-1",
        actions=[
            PlannedAction(
                action_id="create-sprint-action",
                plan_id="run-1",
                action_type="mutation",
                tool_name="create_sprint",
                instruction="Create sprint.",
                input={},
                idempotency_key="k1",
            )
        ],
    )
    action = plan.actions[0]

    sync.record_run_started(
        context,
        "run-1",
        objective="Provision feature",
        prompt_version="1.0.0",
    )
    sync.record_plan_created(plan)
    sync.record_action_state(
        plan,
        action.with_status(ActionStatus.RUNNING),
        event_name="action.executing",
        message="Executing create_sprint.",
    )
    completed_action = action.with_status(ActionStatus.COMPLETED)
    sync.record_action_state(
        plan.replace_action(completed_action),
        completed_action,
        event_name="action.completed",
        message="Completed create_sprint.",
    )

    methods_and_paths = {(method, path) for method, path, _ in prism_client.calls}
    assert prism_client.calls[0] == (
        "POST",
        "/workspaces/w1/agent-runs/internal",
        {
            "agentType": "project_manager",
            "objective": "Provision feature",
            "runId": "run-1",
            "systemPromptVersion": "1.0.0",
            "triggerType": "event",
            "status": "running",
        },
    )
    assert ("POST", "/workspaces/w1/agent-runs/internal") in methods_and_paths
    # Redundant "running" status updates are skipped; only the terminal
    # transition patches the run status.
    status_patches = [
        payload
        for method, path, payload in prism_client.calls
        if method == "PATCH" and path == "/workspaces/w1/agent-runs/internal/run-1/status"
    ]
    assert status_patches == [{"status": "completed"}]
    assert ("POST", "/workspaces/w1/agent-runs/internal/run-1/steps") in methods_and_paths
    assert ("POST", "/workspaces/w1/agent-runs/internal/run-1/actions") in methods_and_paths
    assert any(method == "POST" and path.endswith("/events") for method, path in methods_and_paths)
    step_payloads = [
        payload
        for method, path, payload in prism_client.calls
        if method == "POST" and path.endswith("/steps")
    ]
    action_payloads = [
        payload
        for method, path, payload in prism_client.calls
        if method == "POST" and path.endswith("/actions")
    ]
    assert step_payloads[0]["stepId"] == stable_agent_run_uuid("run-1", "plan")
    assert all(_is_uuid(payload["stepId"]) for payload in step_payloads)
    assert all(_is_uuid(payload["actionId"]) for payload in action_payloads)
    assert all(_is_uuid(payload["stepId"]) for payload in action_payloads if "stepId" in payload)
    assert step_payloads[0]["title"] == "Provision feature"
    assert action_payloads[0] == {
        "actionId": action_api_id(action),
        "actionType": "create_sprint",
        "targetType": "tool",
        "status": "approved",
        "reasoningSummary": "Create sprint.",
        "requiresApproval": False,
    }
    assert "stepId" not in action_payloads[0]
    assert action_payloads[-1]["stepId"] == action_api_id(action)
    assert action_payloads[-1]["status"] == "executed"
    assert action_payloads[-1]["targetType"] == "tool"
    assert "targetId" not in action_payloads[-1]
    assert any(
        path.endswith(f"/{action_api_id(action)}/events") for _, path, _ in prism_client.calls
    )


def test_domain_event_handler_creates_agent_run_before_planning() -> None:
    prism_client = _CapturingPrismClient()
    workflow = WorkflowDefinition(
        id="story.decompose",
        trigger_types=["domain.story.created"],
        required_skills=["task_decomposition"],
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        goal="Break down the story.",
    )
    state_store = MemoryStateStore()
    queue = MemoryQueue()
    source_event = EventEnvelope.wrap(
        DomainEvent(
            event_type="story.created",
            workspace_id="w1",
            project_id="p1",
            correlation_id="run-before-plan",
        )
    )
    context_provider = _StaticContextProvider(source_event, workflow)
    planner = _AssertingPlanner(prism_client)
    handler = DomainEventHandler(
        _StaticRouter(workflow),
        context_provider,
        planner,
        state_store,
        queue,
        AgentRunSync(prism_client, enabled=True),
        Orchestrator(),
        SubAgentRunner(planner),
    )

    handler.handle(source_event)

    assert prism_client.calls[0] == (
        "POST",
        "/workspaces/w1/agent-runs/internal",
        {
            "agentType": "project_manager",
            "objective": "Break down the story.",
            "runId": "run-before-plan",
            "systemPromptVersion": "1.0.0",
            "triggerType": "event",
            "status": "running",
        },
    )
    assert prism_client.calls[1][1].endswith("/run-before-plan/steps")


def test_domain_event_handler_marks_empty_plan_as_failed() -> None:
    prism_client = _CapturingPrismClient()
    workflow = WorkflowDefinition(
        id="feature.provision",
        trigger_types=["domain.feature.provisioning.requested"],
        required_skills=["feature_provisioning"],
        prompt_id="feature.provision",
        prompt_version="1.0.0",
        goal="Provision feature.",
    )
    state_store = MemoryStateStore()
    queue = MemoryQueue()
    source_event = EventEnvelope.wrap(
        DomainEvent(
            event_type="feature.provisioning.requested",
            workspace_id="w1",
            project_id="p1",
            correlation_id="empty-plan-run",
        )
    )
    planner = _EmptyPlanner()
    handler = DomainEventHandler(
        _StaticRouter(workflow),
        _StaticContextProvider(source_event, workflow),
        planner,
        state_store,
        queue,
        AgentRunSync(prism_client, enabled=True),
        Orchestrator(),
        SubAgentRunner(planner),
    )

    handler.handle(source_event)

    plan = state_store.get_plan("w1", "empty-plan-run")
    status_payloads = [payload for method, _, payload in prism_client.calls if method == "PATCH"]
    failed_step_payload = next(
        payload
        for method, path, payload in prism_client.calls
        if method == "POST"
        and path == "/workspaces/w1/agent-runs/internal/empty-plan-run/steps"
        and payload["status"] == "failed"
    )

    assert queue.is_empty()
    assert plan is not None
    assert plan.status == PlanStatus.FAILED
    assert [trace.event_name for trace in state_store.traces][-2:] == [
        "plan.empty",
        "plan.failed",
    ]
    assert state_store.traces[-1].message == (
        "Planner produced no executable actions after retry attempts."
    )
    assert failed_step_payload["errorMessage"] == (
        "Planner produced no executable actions after retry attempts."
    )
    assert status_payloads[-1] == {"status": "failed"}


def test_domain_event_handler_replans_existing_replan_required_run() -> None:
    prism_client = _CapturingPrismClient()
    workflow = WorkflowDefinition(
        id="story.decompose",
        trigger_types=["domain.story.created"],
        required_skills=["task_decomposition"],
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        goal="Break down the story.",
    )
    state_store = MemoryStateStore()
    queue = MemoryQueue()
    source_event = EventEnvelope.wrap(
        DomainEvent(
            event_type="story.created",
            workspace_id="w1",
            project_id="p1",
            payload={"agentRunId": "run-1", "_agent_replan": {"reason": "stale_context"}},
            correlation_id="run-1",
        )
    )
    stale_action = PlannedAction(
        action_id="stale-action",
        plan_id="run-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create stale task.",
        input={},
        idempotency_key="k-stale",
        status=ActionStatus.STALE,
    )
    stale_plan = AgentPlan(
        plan_id="run-1",
        source_event_id="event-1",
        workspace_id="w1",
        project_id="p1",
        goal="old plan",
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx-old",
        actions=[stale_action],
        status=PlanStatus.REPLAN_REQUIRED,
    )
    state_store.save_plan(stale_plan)
    planner = _ReplanPlanner()
    handler = DomainEventHandler(
        _StaticRouter(workflow),
        _StaticContextProvider(source_event, workflow),
        planner,
        state_store,
        queue,
        AgentRunSync(prism_client, enabled=True),
        Orchestrator(),
        SubAgentRunner(planner),
    )

    handler.handle(source_event)

    replanned = state_store.get_plan("w1", "run-1")
    action_message = queue.dequeue()
    assert replanned is not None
    assert replanned.goal == "Break down the story."
    assert replanned.actions[0].action_id == "replacement-action"
    assert action_message is not None
    assert action_message.envelope.event.action_id == "replacement-action"
    assert prism_client.calls[0] == (
        "PATCH",
        "/workspaces/w1/agent-runs/internal/run-1/status",
        {"status": "running"},
    )
    assert not any(
        method == "POST" and path == "/workspaces/w1/agent-runs/internal"
        for method, path, _ in prism_client.calls
    )


def test_agent_run_sync_marks_failed_action_step_and_run() -> None:
    prism_client = _CapturingPrismClient()
    sync = AgentRunSync(prism_client, enabled=True)
    action = PlannedAction(
        action_id="create-workitem-action",
        plan_id="run-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create work item.",
        input={},
        idempotency_key="k1",
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id="event-1",
        workspace_id="w1",
        project_id="p1",
        goal="Provision feature",
        prompt_id="feature.provision",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx-1",
        actions=[action],
    )
    failed_action = action.with_status(ActionStatus.FAILED)
    failed_plan = plan.replace_action(failed_action)

    sync.record_action_state(
        failed_plan,
        failed_action,
        event_name="action.execution_error",
        message="Tool create_workitem failed: assignee not found",
    )

    step_payload = next(
        payload
        for method, path, payload in prism_client.calls
        if method == "POST" and path.endswith("/steps")
    )
    action_payload = next(
        payload
        for method, path, payload in prism_client.calls
        if method == "POST" and path.endswith("/actions")
    )
    status_payloads = [payload for method, path, payload in prism_client.calls if method == "PATCH"]

    assert step_payload["status"] == "failed"
    assert step_payload["errorMessage"] == "Tool create_workitem failed: assignee not found"
    assert action_payload["status"] == "failed"
    assert action_payload["errorMessage"] == "Tool create_workitem failed: assignee not found"
    assert status_payloads[-1] == {"status": "failed"}
    assert any(
        path.endswith(f"/{action_api_id(action)}/events")
        and payload["eventType"] == "action.execution_error"
        for _, path, payload in prism_client.calls
    )


class _CapturingPrismClient(PrismApiClient):
    def __init__(self) -> None:
        super().__init__("https://api.example.test", "secret-token")
        self.calls: list[tuple[str, str, dict | None]] = []

    def update_agent_run_status(self, workspace_id: str, run_id: str, payload: dict) -> dict:
        self.calls.append(
            (
                "PATCH",
                f"/workspaces/{workspace_id}/agent-runs/internal/{run_id}/status",
                payload,
            )
        )
        return {"runId": run_id, "status": payload["status"]}

    def create_agent_run(self, workspace_id: str, payload: dict) -> dict:
        self.calls.append(("POST", f"/workspaces/{workspace_id}/agent-runs/internal", payload))
        return {"runId": payload.get("runId", "run-1"), "status": payload["status"]}

    def upsert_agent_run_step(self, workspace_id: str, run_id: str, payload: dict) -> dict:
        self.calls.append(
            (
                "POST",
                f"/workspaces/{workspace_id}/agent-runs/internal/{run_id}/steps",
                payload,
            )
        )
        return {"stepId": payload.get("stepId", "step-1")}

    def upsert_agent_action(self, workspace_id: str, run_id: str, payload: dict) -> dict:
        self.calls.append(
            (
                "POST",
                f"/workspaces/{workspace_id}/agent-runs/internal/{run_id}/actions",
                payload,
            )
        )
        return {"actionId": payload.get("actionId", "action-1")}

    def create_agent_action_event(self, workspace_id: str, action_id: str, payload: dict) -> dict:
        self.calls.append(
            (
                "POST",
                f"/workspaces/{workspace_id}/agent-actions/internal/{action_id}/events",
                payload,
            )
        )
        return {"eventType": payload["eventType"]}


class _StaticRouter:
    def __init__(self, workflow: WorkflowDefinition) -> None:
        self.workflow = workflow

    def route(self, envelope: EventEnvelope) -> RouteResult:
        _ = envelope
        return RouteResult(
            decision=TriggerPolicyDecision(
                allowed=True,
                trigger_key="domain.story.created",
                reason="allowed",
            ),
            workflow=self.workflow,
        )


class _StaticContextProvider:
    def __init__(self, envelope: EventEnvelope, workflow: WorkflowDefinition) -> None:
        self.snapshot = _context_snapshot(envelope, workflow)

    def hydrate(self, envelope: EventEnvelope, workflow: WorkflowDefinition):
        _ = (envelope, workflow)
        return self.snapshot


class _AssertingPlanner:
    def __init__(self, prism_client: _CapturingPrismClient) -> None:
        self.prism_client = prism_client

    def create_plan(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
        workflow: WorkflowDefinition,
        agent_run_id: str,
    ) -> AgentPlan:
        assert self.prism_client.calls[0][0] == "POST"
        assert self.prism_client.calls[0][1] == "/workspaces/w1/agent-runs/internal"
        action = PlannedAction(
            action_id="action-1",
            plan_id=agent_run_id,
            action_type="analysis",
            tool_name="find_duplicate_workitems",
            instruction="Find duplicates.",
            input={},
            idempotency_key="k1",
        )
        return AgentPlan(
            plan_id=agent_run_id,
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=workflow.goal,
            prompt_id=workflow.prompt_id,
            prompt_version=workflow.prompt_version,
            context_snapshot_ref=context_snapshot_ref,
            actions=[action],
        )


class _EmptyPlanner:
    def create_plan(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
        workflow: WorkflowDefinition,
        agent_run_id: str,
    ) -> AgentPlan:
        return AgentPlan(
            plan_id=agent_run_id,
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=workflow.goal,
            prompt_id=workflow.prompt_id,
            prompt_version=workflow.prompt_version,
            context_snapshot_ref=context_snapshot_ref,
        )


class _ReplanPlanner:
    def create_plan(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
        workflow: WorkflowDefinition,
        agent_run_id: str,
    ) -> AgentPlan:
        action = PlannedAction(
            action_id="replacement-action",
            plan_id=agent_run_id,
            action_type="mutation",
            tool_name="create_workitem",
            instruction="Create replacement task.",
            input={},
            idempotency_key="k-replacement",
        )
        return AgentPlan(
            plan_id=agent_run_id,
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=workflow.goal,
            prompt_id=workflow.prompt_id,
            prompt_version=workflow.prompt_version,
            context_snapshot_ref=context_snapshot_ref,
            actions=[action],
        )


def _context_snapshot(envelope: EventEnvelope, workflow: WorkflowDefinition):
    from domain.context import ContextSnapshot

    return ContextSnapshot(
        workspace_id=envelope.event.workspace_id,
        project_id=envelope.event.project_id,
        workflow_id=workflow.workflow_id,
        context=AgentContext(
            workspace_id=envelope.event.workspace_id,
            project_id=envelope.event.project_id,
            source_event=envelope,
            workflow_id=workflow.workflow_id,
        ),
    )


def _is_uuid(value: str) -> bool:
    UUID(value)
    return True
