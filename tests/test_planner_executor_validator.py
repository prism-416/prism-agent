import os

import pytest

from application.context_provider import ContextProvider
from application.executor import Executor
from application.planner import Planner
from application.validator import Validator
from domain.actions import ActionStatus, PlannedAction
from domain.events import DomainEvent, EventEnvelope
from domain.plans import AgentPlan, PlanStatus
from domain.results import ValidationDecision
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.llm.pydantic_ai_agent_factory import (
    PydanticAIAgentFactory,
    RuntimePlanningAgent,
)
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowDefinition, WorkflowRegistry


def test_planner_outputs_agent_plan_schema(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("story.decompose")
    event = DomainEvent(
        event_type="story.created",
        workspace_id="w1",
        project_id="p1",
        correlation_id="story-run-1",
        payload={
            "story": {"id": "s1", "title": "Invite teammates", "version": 1},
            "entity_versions": {"story:s1": 1},
        },
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    planner = Planner(
        prompt_registry,
        SkillRegistry.from_prompt_registry(prompt_registry),
        ToolRegistry.from_prompt_registry(prompt_registry),
        PydanticAIAgentFactory(GeminiModelProvider(Settings())),
    )

    plan = planner.create_plan(snapshot.context, snapshot.ref, workflow, "story-run-1")

    assert plan.plan_id == "story-run-1"
    assert plan.prompt_id == "story.decompose"
    assert plan.source_event_id == event.event_id
    assert len(plan.actions) >= 1
    assert plan.actions[0].plan_id == plan.plan_id
    assert set(plan.tool_names).issubset(
        set(
            SkillRegistry.from_prompt_registry(prompt_registry).allowed_tools_for(
                workflow.required_skills
            )
        )
    )


def test_agent_factory_composes_workflow_skill_and_tool_prompts(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow_prompt = prompt_registry.get_workflow("story.decompose", "1.0.0")
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry)
    skills = skill_registry.select(["task_decomposition"])
    tools = tool_registry.select(skill_registry.allowed_tools_for(["task_decomposition"]))
    agent = PydanticAIAgentFactory(GeminiModelProvider(Settings())).create_agent(
        workflow_prompt, skills, tools
    )

    compiled = agent.compile_system_prompt()

    assert "Workflow: Story Decomposition" in compiled
    assert "Capability: Task Decomposition" in compiled
    assert "Tool: Create Work Item" in compiled
    assert "Input contract:" in compiled
    assert "assigneeUsernames" in compiled
    assert "member_workloads" in compiled


def test_gemini_provider_uses_google_model_prefix(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow_prompt = prompt_registry.get_workflow("story.decompose", "1.0.0")

    model_ref = GeminiModelProvider(Settings()).pydantic_ai_model_ref(workflow_prompt)

    assert model_ref.startswith("google:")
    assert not model_ref.startswith("google-gla:")


def test_gemini_provider_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY or GOOGLE_API_KEY is required"):
        GeminiModelProvider(Settings()).configure_environment()


def test_gemini_provider_prefers_gemini_key_without_google_duplicate(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "stale-google")

    GeminiModelProvider(Settings(gemini_api_key="test-gemini")).configure_environment()

    assert os.environ["GEMINI_API_KEY"] == "test-gemini"
    assert "GOOGLE_API_KEY" not in os.environ


def test_runtime_agent_returns_gemini_plan_without_hardcoded_fallback(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow_prompt = prompt_registry.get_workflow("feature.provision", "1.0.0")
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("feature.provision")
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="w1",
        project_id="p1",
        payload={"featureSpecification": "Add roadmap import."},
        idempotency_key="req-live",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry)
    tools = tool_registry.select(skill_registry.allowed_tools_for(["feature_provisioning"]))
    agent = _EmptyLiveRuntimePlanningAgent(
        workflow_prompt,
        skill_registry.select(["feature_provisioning"]),
        tools,
        GeminiModelProvider(Settings()),
    )

    plan = agent.generate_plan(
        snapshot.context,
        snapshot.ref,
        Planner._approval_policy(workflow.approval_policy),
    )

    assert plan.actions == []


def test_runtime_agent_retries_empty_plan_generation(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow_prompt = prompt_registry.get_workflow("feature.provision", "1.0.0")
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("feature.provision")
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="w1",
        project_id="p1",
        payload={"featureSpecification": "Add roadmap import."},
        idempotency_key="req-live",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry)
    tools = tool_registry.select(skill_registry.allowed_tools_for(["feature_provisioning"]))
    agent = _RetryingRuntimePlanningAgent(
        workflow_prompt,
        skill_registry.select(["feature_provisioning"]),
        tools,
        GeminiModelProvider(Settings()),
    )

    plan = agent.generate_plan(
        snapshot.context,
        snapshot.ref,
        Planner._approval_policy(workflow.approval_policy),
    )

    assert agent.attempts == 2
    assert len(plan.actions) == 1
    assert plan.actions[0].tool_name == "create_workitem"
    assert "Planning retry feedback:" not in agent.user_prompts[0]
    assert "Planning retry feedback:" in agent.user_prompts[1]


def test_planner_rejects_tools_not_allowed_by_selected_skills(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowDefinition(
        id="risk.only",
        trigger_types=["domain.story.created"],
        required_skills=["risk_detection"],
        prompt_id="story.decompose",
        prompt_version="1.0.0",
    )
    event = DomainEvent(
        event_type="story.created",
        workspace_id="w1",
        project_id="p1",
        correlation_id="story-run-2",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    planner = Planner(
        prompt_registry,
        SkillRegistry.from_prompt_registry(prompt_registry),
        ToolRegistry.from_prompt_registry(prompt_registry),
        _DisallowedToolAgentFactory(),
    )

    with pytest.raises(ValueError, match="not allowed by selected skills"):
        planner.create_plan(snapshot.context, snapshot.ref, workflow, "story-run-2")


class _EmptyLiveRuntimePlanningAgent(RuntimePlanningAgent):
    def _generate_plan_with_pydantic_ai(self, context, context_snapshot_ref):
        return AgentPlan(
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=self.workflow_prompt.goal,
            prompt_id=self.workflow_prompt.id,
            prompt_version=self.workflow_prompt.version,
            context_snapshot_ref=context_snapshot_ref,
        )


class _RetryingRuntimePlanningAgent(RuntimePlanningAgent):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.attempts = 0
        self.user_prompts: list[str] = []

    def _generate_plan_with_pydantic_ai(self, context, context_snapshot_ref):
        self.attempts += 1
        self.user_prompts.append(self._render_user_prompt(context, context_snapshot_ref))
        plan = AgentPlan(
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=self.workflow_prompt.goal,
            prompt_id=self.workflow_prompt.id,
            prompt_version=self.workflow_prompt.version,
            context_snapshot_ref=context_snapshot_ref,
        )
        if self.attempts == 1:
            return plan
        return plan.model_copy(
            update={
                "actions": [
                    PlannedAction(
                        plan_id=plan.plan_id,
                        action_type="mutation",
                        tool_name="create_workitem",
                        instruction="Create the requested feature work item.",
                        input={
                            "projectId": context.project_id,
                            "title": "Roadmap import",
                            "description": "Add roadmap import.",
                        },
                        idempotency_key="retry:create_workitem:1",
                    )
                ]
            }
        )


def test_planner_normalizes_live_llm_runtime_control_fields(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("feature.provision")
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="w1",
        project_id="p1",
        payload={
            "featureSpecification": "Add roadmap import.",
            "workspace": {"workspaceId": "w1"},
            "project": {"projectId": "p1"},
        },
        idempotency_key="req-live",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    planner = Planner(
        prompt_registry,
        SkillRegistry.from_prompt_registry(prompt_registry),
        ToolRegistry.from_prompt_registry(prompt_registry),
        _UnnormalizedAllowedToolAgentFactory(),
    )

    plan = planner.create_plan(snapshot.context, snapshot.ref, workflow, "req-live")

    assert plan.plan_id == "req-live"
    assert plan.source_event_id == event.event_id
    assert plan.workspace_id == "w1"
    assert plan.project_id == "p1"
    assert plan.prompt_id == "feature.provision"
    assert plan.context_snapshot_ref == snapshot.ref
    assert plan.status == PlanStatus.PLANNED
    assert [action.tool_name for action in plan.actions] == ["create_sprint", "create_workitem"]
    assert [action.status for action in plan.actions] == [
        ActionStatus.PENDING,
        ActionStatus.PENDING,
    ]
    assert [action.requires_approval for action in plan.actions] == [False, False]
    assert [action.plan_id for action in plan.actions] == [plan.plan_id, plan.plan_id]
    assert plan.actions[0].depends_on == []
    assert plan.actions[1].depends_on == [plan.actions[0].action_id]
    assert [action.idempotency_key for action in plan.actions] == [
        "req-live:create_sprint:1",
        "req-live:create_workitem:2",
    ]


class _DisallowedToolAgentFactory:
    def create_agent(self, workflow_prompt, skills, tools):
        return _DisallowedToolAgent(workflow_prompt)


class _DisallowedToolAgent:
    def __init__(self, workflow_prompt) -> None:
        self.workflow_prompt = workflow_prompt

    def generate_plan(self, context, context_snapshot_ref, approval_policy):
        _ = approval_policy
        plan = AgentPlan(
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=self.workflow_prompt.goal,
            prompt_id=self.workflow_prompt.id,
            prompt_version=self.workflow_prompt.version,
            skill_ids=["risk_detection"],
            tool_names=["create_workitem"],
            context_snapshot_ref=context_snapshot_ref,
        )
        return plan.model_copy(
            update={
                "actions": [
                    PlannedAction(
                        plan_id=plan.plan_id,
                        action_type="mutation",
                        tool_name="create_workitem",
                        instruction="This tool is not allowed by risk_detection.",
                        input={},
                        idempotency_key="bad-tool",
                    )
                ]
            }
        )


class _UnnormalizedAllowedToolAgentFactory:
    def create_agent(self, workflow_prompt, skills, tools):
        _ = (skills, tools)
        return _UnnormalizedAllowedToolAgent(workflow_prompt)


class _UnnormalizedAllowedToolAgent:
    def __init__(self, workflow_prompt) -> None:
        self.workflow_prompt = workflow_prompt

    def generate_plan(self, context, context_snapshot_ref, approval_policy):
        _ = (context, context_snapshot_ref, approval_policy)
        plan = AgentPlan(
            source_event_id="llm-wrong-event",
            workspace_id="llm-wrong-workspace",
            project_id="llm-wrong-project",
            goal="llm goal",
            prompt_id="llm.prompt",
            prompt_version="0.0.0",
            skill_ids=["llm_skill"],
            tool_names=["create_sprint", "create_workitem"],
            context_snapshot_ref="llm-snapshot",
            status=PlanStatus.COMPLETED,
        )
        return plan.model_copy(
            update={
                "plan_id": "llm-plan",
                "actions": [
                    PlannedAction(
                        plan_id="llm-plan",
                        action_type="mutation",
                        tool_name="create_sprint",
                        instruction="Create sprint.",
                        input={"name": "Roadmap import"},
                        depends_on=["missing-action"],
                        requires_approval=True,
                        status=ActionStatus.COMPLETED,
                        idempotency_key="llm-key-1",
                    ),
                    PlannedAction(
                        plan_id="llm-plan",
                        action_type="mutation",
                        tool_name="create_workitem",
                        instruction="Create work item.",
                        input={"title": "Roadmap import"},
                        depends_on=["missing-action"],
                        requires_approval=True,
                        status=ActionStatus.COMPLETED,
                        idempotency_key="llm-key-2",
                    ),
                ],
            }
        )


def test_executor_runs_exactly_one_action(prompts_path) -> None:
    event = DomainEvent(
        event_type="story.created",
        workspace_id="w1",
        project_id="p1",
        payload={"entity_versions": {"story:s1": 1}},
    )
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("story.decompose")
    context = (
        ContextProvider(PrismApiClient(), prompt_registry)
        .hydrate(EventEnvelope.wrap(event), workflow)
        .context
    )
    action = PlannedAction(
        plan_id="plan-1",
        action_type="suggestion",
        tool_name="create_agent_suggestion",
        instruction="Suggest story tasks.",
        input={"title": "Suggested tasks", "body": "Create task suggestions."},
        idempotency_key="k1",
        expected_entity_versions={"story:s1": 1},
    )

    result = Executor(ToolRegistry.from_prompt_registry(prompt_registry)).execute_one(
        action, context
    )

    assert result.success is True
    assert result.action_id == action.action_id
    assert result.suggestion is not None


def test_link_pull_request_tool_returns_planned_pr_identity(prompts_path) -> None:
    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="w1",
        project_id="p1",
        payload={},
    )
    workflow = WorkflowRegistry.with_defaults().get("pr.status_sync")
    context = (
        ContextProvider(PrismApiClient(), PromptRegistry(prompts_path))
        .hydrate(EventEnvelope.wrap(event), workflow)
        .context
    )
    action = PlannedAction(
        plan_id="plan-1",
        action_type="link",
        tool_name="link_pull_request",
        instruction="Record the PR relationship.",
        input={"projectId": "p1", "itemId": "item-1", "pull_request_id": "pr-1"},
        idempotency_key="k1",
    )

    result = Executor(ToolRegistry.from_prompt_registry(PromptRegistry(prompts_path))).execute_one(
        action,
        context,
    )

    assert result.success is True
    assert result.output["itemId"] == "item-1"
    assert result.output["pull_request_id"] == "pr-1"


def test_validator_detects_stale_context() -> None:
    prism_client = PrismApiClient()
    prism_client.set_entity_version("story:s1", 2)
    event = DomainEvent(
        event_type="story.created",
        workspace_id="w1",
        project_id="p1",
        payload={"entity_versions": {"story:s1": 1}},
    )
    context = (
        ContextProvider(PrismApiClient(), PromptRegistry())
        .hydrate(
            EventEnvelope.wrap(event),
            WorkflowRegistry.with_defaults().get("story.decompose"),
        )
        .context
    )
    action = PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create a task.",
        input={},
        idempotency_key="k1",
        expected_entity_versions={"story:s1": 1},
    )

    validation = Validator(prism_client).validate_before_execution(action, context)

    assert validation.decision == ValidationDecision.REPLAN
    assert validation.stale_entities["story:s1"]["current"] == 2
