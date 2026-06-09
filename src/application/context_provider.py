from __future__ import annotations

from domain.context import AgentContext, ContextSnapshot
from domain.events import EventEnvelope
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.workflow_registry import WorkflowDefinition


def _required_context(workflow: WorkflowDefinition, declared: list[str]) -> list[str]:
    """Narrow hydration to what an orchestrated workflow's subagents actually need.

    For a non-orchestrated workflow (or one whose any node wants the full context,
    i.e. an empty scope) this returns the declared ``required_context`` unchanged.
    Otherwise it fetches only the declared entities that appear in some node scope —
    lazy, scoped hydration. Synthetic scope keys (e.g. ``subagent_results``) are not
    in ``required_context`` and are injected at synthesizer planning time instead.
    """
    orchestration = workflow.orchestration
    if orchestration is None or not orchestration.default_graph:
        return declared
    nodes = list(orchestration.default_graph)
    if orchestration.synthesizer is not None:
        nodes.append(orchestration.synthesizer)
    scoped: set[str] = set()
    for node in nodes:
        if not node.context_scope:
            return declared  # a node wants the full context
        scoped.update(node.context_scope)
    return [entity for entity in declared if entity in scoped]


class ContextProvider:
    def __init__(self, prism_client: PrismApiClient, prompt_registry: PromptRegistry) -> None:
        self.prism_client = prism_client
        self.prompt_registry = prompt_registry

    def hydrate(self, envelope: EventEnvelope, workflow: WorkflowDefinition) -> ContextSnapshot:
        prompt = self.prompt_registry.get_workflow(workflow.prompt_id, workflow.prompt_version)
        required_context = _required_context(workflow, prompt.required_context)
        entities = self.prism_client.fetch_context_entities(envelope.event, required_context)
        permissions = self.prism_client.fetch_permissions(envelope.event)
        entity_versions = self.prism_client.capture_entity_versions(envelope.event, entities)
        context = AgentContext(
            workspace_id=envelope.event.workspace_id,
            project_id=envelope.event.project_id,
            source_event=envelope,
            workflow_id=workflow.workflow_id,
            entities=entities,
            retrieved_documents=envelope.event.payload.get("retrieved_documents", []),
            permissions=permissions,
            entity_versions=entity_versions,
            previous_agent_outputs=envelope.event.payload.get("previous_agent_outputs", []),
            runtime_metadata={
                "trigger_event_type": envelope.event.event_type,
                "trigger_kind": envelope.event.kind,
            },
        )
        return ContextSnapshot(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            workflow_id=context.workflow_id,
            context=context,
        )
