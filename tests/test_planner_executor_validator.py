import pytest

from application.context_provider import ContextProvider
from application.executor import Executor
from application.planner import Planner
from application.validator import Validator
from domain.actions import PlannedAction
from domain.events import DomainEvent, EventEnvelope
from domain.plans import AgentPlan
from domain.results import ValidationDecision
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.llm.pydantic_ai_agent_factory import PydanticAIAgentFactory
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
        PydanticAIAgentFactory(GeminiModelProvider(Settings(prompts_path=str(prompts_path)))),
    )

    plan = planner.create_plan(snapshot.context, snapshot.ref, workflow)

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
    agent = PydanticAIAgentFactory(
        GeminiModelProvider(Settings(prompts_path=str(prompts_path)))
    ).create_agent(workflow_prompt, skills, tools)

    compiled = agent.compile_system_prompt()

    assert "Workflow: Story Decomposition" in compiled
    assert "Capability: Task Decomposition" in compiled
    assert "Tool: Create Work Item" in compiled


def test_planner_rejects_tools_not_allowed_by_selected_skills(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowDefinition(
        id="risk.only",
        trigger_types=["domain.story.created"],
        required_skills=["risk_detection"],
        prompt_id="story.decompose",
        prompt_version="1.0.0",
    )
    event = DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
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
        planner.create_plan(snapshot.context, snapshot.ref, workflow)


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
        ContextProvider(PrismApiClient(), PromptRegistry("prompts"))
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
