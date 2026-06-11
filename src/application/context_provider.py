from __future__ import annotations

import logging

from domain.context import AgentContext, ContextSnapshot
from domain.events import BaseRuntimeEvent, EventEnvelope
from infrastructure.object_storage.base import JsonPayloadStore
from infrastructure.observability.logging_config import get_logger, log_json
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
    def __init__(
        self,
        prism_client: PrismApiClient,
        prompt_registry: PromptRegistry,
        diff_payload_store: JsonPayloadStore | None = None,
    ) -> None:
        self.prism_client = prism_client
        self.prompt_registry = prompt_registry
        self.diff_payload_store = diff_payload_store

    def hydrate(self, envelope: EventEnvelope, workflow: WorkflowDefinition) -> ContextSnapshot:
        prompt = self.prompt_registry.get_workflow(workflow.prompt_id, workflow.prompt_version)
        required_context = _required_context(workflow, prompt.required_context)
        # PR-review events carry the diff as a pointer (diffObjectName) to an
        # object-storage blob, not inline. Dereference it here so the diff and PR
        # metadata hydrate through the normal entity path below.
        hydration_event = self._resolve_diff_pointer(envelope.event, required_context)
        entities = self.prism_client.fetch_context_entities(hydration_event, required_context)
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

    def _resolve_diff_pointer(
        self, event: BaseRuntimeEvent, required_context: list[str]
    ) -> BaseRuntimeEvent:
        """Dereference a pull-request-diff object-storage pointer into the payload.

        The dispatch envelope is a lightweight routing message: it references the
        heavy diff blob by ``diffObjectName`` (+ ``diffObjectVersionId``) rather
        than embedding it. When the diff is needed and not already inline, fetch
        the blob and inject its ``pullRequest`` under ``pull_request_diff`` (and
        ``pullRequest``) so the diff and PR metadata hydrate through the normal
        entity path. Returns the event unchanged on any miss so hydration degrades
        to the existing fallbacks instead of failing.
        """
        if self.diff_payload_store is None or "pull_request_diff" not in required_context:
            return event
        payload = event.payload
        object_name = payload.get("diffObjectName") or payload.get("diff_object_name")
        if not object_name:
            return event
        # Already inline (local runs / future-proofing): let the entity hydration
        # extract it from the embedded object instead of re-fetching.
        if payload.get("pull_request_diff") is not None or payload.get("pullRequest") is not None:
            return event
        version_id = payload.get("diffObjectVersionId") or payload.get("diff_object_version_id")
        record = {
            "log": "diff.pointer",
            "event_type": event.event_type,
            "workspace_id": event.workspace_id,
            "object_name": object_name,
        }
        try:
            blob = self.diff_payload_store.fetch_json(object_name, version_id)
        except Exception as exc:  # object storage failures must not abort hydration
            log_json(
                get_logger(),
                logging.WARNING,
                {**record, "resolved": False, "error_type": type(exc).__name__, "error": str(exc)},
            )
            return event
        pull_request = blob.get("pullRequest") or blob.get("pull_request")
        if not isinstance(pull_request, dict):
            log_json(
                get_logger(),
                logging.WARNING,
                {**record, "resolved": False, "reason": "blob_missing_pull_request"},
            )
            return event
        files = pull_request.get("files")
        log_json(
            get_logger(),
            logging.INFO,
            {
                **record,
                "resolved": True,
                "files_count": len(files) if isinstance(files, list) else None,
            },
        )
        merged_payload = {**payload, "pull_request_diff": pull_request, "pullRequest": pull_request}
        return event.model_copy(update={"payload": merged_payload})
