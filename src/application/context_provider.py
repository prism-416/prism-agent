from __future__ import annotations

from domain.context import AgentContext, ContextSnapshot
from domain.events import EventEnvelope
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.workflow_registry import WorkflowDefinition


class ContextProvider:
    def __init__(self, prism_client: PrismApiClient, prompt_registry: PromptRegistry) -> None:
        self.prism_client = prism_client
        self.prompt_registry = prompt_registry

    def hydrate(self, envelope: EventEnvelope, workflow: WorkflowDefinition) -> ContextSnapshot:
        prompt = self.prompt_registry.get_workflow(workflow.prompt_id, workflow.prompt_version)
        entities = self.prism_client.fetch_context_entities(envelope.event, prompt.required_context)
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
