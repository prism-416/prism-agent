from __future__ import annotations

from application.context_provider import _required_context
from application.orchestrator import Orchestrator
from capabilities.definitions import OrchestrationDef, OrchestrationNodeDef
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.workflow_registry import WorkflowDefinition, WorkflowRegistry


def _context() -> AgentContext:
    event = DomainEvent(event_type="sprint.ended", workspace_id="w1", project_id="p1")
    return AgentContext(
        workspace_id="w1",
        project_id="p1",
        source_event=EventEnvelope.wrap(event),
        workflow_id="sprint.report",
    )


# -- model tier resolution -------------------------------------------------


def test_provider_resolves_role_tiers_to_models() -> None:
    settings = Settings(default_gemini_model="pro-model", gemini_flash_model="flash-model")
    provider = GeminiModelProvider(settings)
    prompt = PromptRegistry().get_workflow("sprint.report", "1.0.0")

    assert provider.pydantic_ai_model_ref(prompt, "pro") == "google:pro-model"
    assert provider.pydantic_ai_model_ref(prompt, "flash") == "google:flash-model"
    # Unknown / absent tier falls back to the workflow prompt's own model.
    assert provider.pydantic_ai_model_ref(prompt, "unknown") == provider.pydantic_ai_model_ref(
        prompt
    )
    assert provider.pydantic_ai_model_ref(prompt, None) == provider.pydantic_ai_model_ref(prompt)


def test_sprint_report_graph_assigns_role_tiers() -> None:
    prompt_registry = PromptRegistry()
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("sprint.report")
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)

    graph = Orchestrator(skill_registry).build_orchestrated_graph(_context(), workflow)

    tiers = {node.node_id: node.model_tier for node in graph.nodes}
    assert tiers == {"summary": "pro", "risk": "flash"}
    assert graph.synthesizer_node is not None
    assert graph.synthesizer_node.model_tier == "pro"


# -- lazy scoped hydration -------------------------------------------------


def _scoped_workflow(nodes: list[OrchestrationNodeDef], synth=None) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf",
        trigger_types=["domain.x"],
        required_skills=["s"],
        prompt_id="wf",
        prompt_version="1.0.0",
        orchestration=OrchestrationDef(default_graph=nodes, synthesizer=synth),
    )


def test_required_context_narrows_to_union_of_node_scopes() -> None:
    workflow = _scoped_workflow(
        [
            OrchestrationNodeDef(node="a", skill="s", context_scope=["alpha", "beta"]),
            OrchestrationNodeDef(node="b", skill="s", context_scope=["beta", "gamma"]),
        ]
    )

    narrowed = _required_context(workflow, ["alpha", "beta", "gamma", "delta"])

    assert set(narrowed) == {"alpha", "beta", "gamma"}  # delta is fetched by no node


def test_required_context_includes_synthesizer_scope() -> None:
    workflow = _scoped_workflow(
        [OrchestrationNodeDef(node="a", skill="s", context_scope=["alpha"])],
        synth=OrchestrationNodeDef(skill="s", context_scope=["subagent_results", "gamma"]),
    )

    narrowed = _required_context(workflow, ["alpha", "beta", "gamma"])

    # subagent_results is synthetic (not in required_context) and never fetched.
    assert set(narrowed) == {"alpha", "gamma"}


def test_required_context_full_when_a_node_is_unscoped() -> None:
    workflow = _scoped_workflow(
        [
            OrchestrationNodeDef(node="a", skill="s", context_scope=["alpha"]),
            OrchestrationNodeDef(node="b", skill="s"),  # empty scope = full context
        ]
    )

    declared = ["alpha", "beta", "gamma"]
    assert _required_context(workflow, declared) == declared


def test_required_context_unchanged_without_orchestration() -> None:
    workflow = WorkflowDefinition(
        id="wf",
        trigger_types=["domain.x"],
        required_skills=["s"],
        prompt_id="wf",
        prompt_version="1.0.0",
    )
    declared = ["alpha", "beta"]
    assert _required_context(workflow, declared) == declared
