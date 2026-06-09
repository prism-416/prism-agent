from __future__ import annotations

import pytest

from application.context_provider import ContextProvider
from application.orchestrator import (
    DEFAULT_MODEL_TIER,
    ROOT_NODE_ID,
    SYNTHESIZER_NODE_ID,
    Orchestrator,
)
from application.planner import Planner, _scope_context
from application.subagent_runner import sub_plan_id
from capabilities.definitions import (
    OrchestrationDef,
    OrchestrationNodeDef,
)
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowDefinition


def _context() -> AgentContext:
    event = DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
    return AgentContext(
        workspace_id="w1",
        project_id="p1",
        source_event=EventEnvelope.wrap(event),
        workflow_id="story.decompose",
        entities={"work_item": {"id": "s1"}, "recent_sprints": [], "project_members": []},
    )


def _orchestrated_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="story.decompose",
        trigger_types=["domain.story.created"],
        required_skills=["task_decomposition", "risk_detection", "backlog_analysis"],
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        goal="Break down the story.",
        orchestration=OrchestrationDef(
            mode="hybrid",
            default_graph=[
                OrchestrationNodeDef(
                    node="decompose",
                    skill="task_decomposition",
                    context_scope=["work_item", "project_members"],
                ),
                OrchestrationNodeDef(
                    node="risk",
                    skill="risk_detection",
                    context_scope=["work_item", "recent_sprints"],
                    model_tier="flash",
                ),
            ],
            synthesizer=OrchestrationNodeDef(
                skill="backlog_analysis",
                context_scope=["subagent_results"],
            ),
        ),
    )


def test_build_orchestrated_graph_expands_nodes_and_synthesizer() -> None:
    graph = Orchestrator().build_orchestrated_graph(_context(), _orchestrated_workflow())

    assert [node.node_id for node in graph.nodes] == ["decompose", "risk"]
    decompose, risk = graph.nodes
    assert decompose.skill_ids == ["task_decomposition"]
    assert decompose.context_scope == ["work_item", "project_members"]
    assert decompose.model_tier == DEFAULT_MODEL_TIER
    assert risk.model_tier == "flash"
    assert risk.depends_on == []

    assert graph.synthesizer_node is not None
    synth = graph.synthesizer_node
    assert synth.node_id == SYNTHESIZER_NODE_ID
    assert synth.skill_ids == ["backlog_analysis"]
    # Synthesizer defaults to depending on every graph node.
    assert synth.depends_on == ["decompose", "risk"]


def test_build_orchestrated_graph_falls_back_to_single_node_without_block() -> None:
    workflow = WorkflowDefinition(
        id="pr.status_sync",
        trigger_types=["domain.pr.opened"],
        required_skills=["pr_status_sync"],
        prompt_id="pr.status_sync",
        prompt_version="1.0.0",
        goal="Sync PR status.",
    )

    graph = Orchestrator().build_orchestrated_graph(_context(), workflow)

    assert len(graph.nodes) == 1
    assert graph.nodes[0].node_id == ROOT_NODE_ID
    assert graph.synthesizer_node is None


def test_orchestrator_resolves_tier_from_skill_registry() -> None:
    workflow = _orchestrated_workflow().model_copy(
        update={
            "orchestration": OrchestrationDef(
                default_graph=[OrchestrationNodeDef(node="decompose", skill="task_decomposition")]
            )
        }
    )
    skill_registry = SkillRegistry.from_prompt_registry(PromptRegistry())

    graph = Orchestrator(skill_registry).build_orchestrated_graph(_context(), workflow)

    expected = skill_registry.model_tier("task_decomposition")
    assert graph.nodes[0].model_tier == expected


def test_node_without_skill_raises() -> None:
    workflow = _orchestrated_workflow().model_copy(
        update={"orchestration": OrchestrationDef(default_graph=[OrchestrationNodeDef(node="x")])}
    )

    with pytest.raises(ValueError, match="declares no skill"):
        Orchestrator().build_orchestrated_graph(_context(), workflow)


def test_scope_context_slices_entities_only() -> None:
    context = _context()
    scoped = _scope_context(context, ["work_item"])

    assert set(scoped.entities) == {"work_item"}
    assert scoped.entity_versions == context.entity_versions
    assert scoped.permissions == context.permissions

    assert _scope_context(context, []) is context
    assert _scope_context(context, None) is context


def test_sub_plan_id_is_node_qualified() -> None:
    assert sub_plan_id("run-1", "risk") == "run-1::risk"


def test_planner_scopes_tools_to_node_skill_subset(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowDefinition(
        id="story.decompose",
        trigger_types=["domain.story.created"],
        required_skills=["task_decomposition", "risk_detection"],
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        goal="Break down the story.",
    )
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    planner = Planner(
        prompt_registry,
        skill_registry,
        ToolRegistry.from_prompt_registry(prompt_registry),
        _CapturingAgentFactory(),
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(
            DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
        ),
        workflow,
    )

    planner.create_plan(
        snapshot.context,
        snapshot.ref,
        workflow,
        "run-1",
        plan_id="run-1::risk",
        skill_ids=["risk_detection"],
        context_scope=["work_item"],
    )

    factory = planner.agent_factory
    selected_tools = {tool.name for tool in factory.tools}
    expected_tools = set(skill_registry.allowed_tools_for(["risk_detection"]))
    assert selected_tools == expected_tools
    assert {skill.id for skill in factory.skills} == {"risk_detection"}
    assert set(factory.context.entities) <= {"work_item"}


def test_live_planner_default_path_unchanged(prompts_path) -> None:
    """A whole-workflow plan keeps its original plan_id/idempotency prefix."""
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowDefinition(
        id="feature.provision",
        trigger_types=["domain.feature.provisioning.requested"],
        required_skills=["feature_provisioning"],
        prompt_id="feature.provision",
        prompt_version="1.0.0",
        goal="Provision feature.",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(
            DomainEvent(
                event_type="feature.provisioning.requested",
                workspace_id="w1",
                project_id="p1",
                idempotency_key="req-live",
            )
        ),
        workflow,
    )
    planner = Planner(
        prompt_registry,
        SkillRegistry.from_prompt_registry(prompt_registry),
        ToolRegistry.from_prompt_registry(prompt_registry),
        _TwoActionAgentFactory(),
    )

    plan = planner.create_plan(snapshot.context, snapshot.ref, workflow, "req-live")

    assert plan.plan_id == "req-live"
    assert [a.idempotency_key for a in plan.actions] == [
        "req-live:create_sprint:1",
        "req-live:create_workitem:2",
    ]


def test_planner_sub_plan_uses_plan_id_idempotency_prefix(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowDefinition(
        id="feature.provision",
        trigger_types=["domain.feature.provisioning.requested"],
        required_skills=["feature_provisioning"],
        prompt_id="feature.provision",
        prompt_version="1.0.0",
        goal="Provision feature.",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(
            DomainEvent(
                event_type="feature.provisioning.requested",
                workspace_id="w1",
                project_id="p1",
                idempotency_key="req-live",
            )
        ),
        workflow,
    )
    planner = Planner(
        prompt_registry,
        SkillRegistry.from_prompt_registry(prompt_registry),
        ToolRegistry.from_prompt_registry(prompt_registry),
        _TwoActionAgentFactory(),
    )

    plan = planner.create_plan(
        snapshot.context,
        snapshot.ref,
        workflow,
        "req-live",
        plan_id="req-live::provision",
        skill_ids=["feature_provisioning"],
    )

    assert plan.plan_id == "req-live::provision"
    assert [a.idempotency_key for a in plan.actions] == [
        "req-live::provision:create_sprint:1",
        "req-live::provision:create_workitem:2",
    ]
    assert all(a.plan_id == "req-live::provision" for a in plan.actions)


class _CapturingAgentFactory:
    def __init__(self) -> None:
        self.skills: list = []
        self.tools: list = []
        self.context: AgentContext | None = None

    def create_agent(self, workflow_prompt, skills, tools, model_tier=None):
        self.skills = skills
        self.tools = tools
        self.model_tier = model_tier
        return _CapturingAgent(workflow_prompt, self)


class _CapturingAgent:
    def __init__(self, workflow_prompt, factory: _CapturingAgentFactory) -> None:
        self.workflow_prompt = workflow_prompt
        self.factory = factory

    def generate_plan(self, context, context_snapshot_ref, approval_policy):
        from domain.plans import AgentPlan

        self.factory.context = context
        return AgentPlan(
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=self.workflow_prompt.goal,
            prompt_id=self.workflow_prompt.id,
            prompt_version=self.workflow_prompt.version,
            context_snapshot_ref=context_snapshot_ref,
        )


class _TwoActionAgentFactory:
    def create_agent(self, workflow_prompt, skills, tools, model_tier=None):
        _ = (skills, tools, model_tier)
        return _TwoActionAgent(workflow_prompt)


class _TwoActionAgent:
    def __init__(self, workflow_prompt) -> None:
        self.workflow_prompt = workflow_prompt

    def generate_plan(self, context, context_snapshot_ref, approval_policy):
        _ = approval_policy
        from domain.actions import PlannedAction
        from domain.plans import AgentPlan

        plan = AgentPlan(
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=self.workflow_prompt.goal,
            prompt_id=self.workflow_prompt.id,
            prompt_version=self.workflow_prompt.version,
            context_snapshot_ref=context_snapshot_ref,
        )
        return plan.model_copy(
            update={
                "actions": [
                    PlannedAction(
                        plan_id=plan.plan_id,
                        action_type="mutation",
                        tool_name="create_sprint",
                        instruction="Create sprint.",
                        input={},
                        idempotency_key="seed-1",
                    ),
                    PlannedAction(
                        plan_id=plan.plan_id,
                        action_type="mutation",
                        tool_name="create_workitem",
                        instruction="Create work item.",
                        input={},
                        idempotency_key="seed-2",
                    ),
                ]
            }
        )
