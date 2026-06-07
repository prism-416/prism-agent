from __future__ import annotations

from typing import Any

from domain.actions import PlannedAction
from domain.context import AgentContext, ContextSnapshot
from domain.events import BaseRuntimeEvent, EventEnvelope
from domain.plans import AgentPlan
from domain.results import ToolResult, ValidationResult


def action_trace_data(
    action: PlannedAction,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "action_id": action.action_id,
        "action_type": action.action_type,
        "tool_name": action.tool_name,
        "instruction": action.instruction,
        "input": action.input,
        "depends_on": action.depends_on,
        "requires_approval": action.requires_approval,
        "status": action.status.value,
        "idempotency_key": action.idempotency_key,
        "expected_entity_versions": action.expected_entity_versions,
    }
    if extra:
        data.update(extra)
    return data


def plan_trace_data(
    plan: AgentPlan,
    snapshot: ContextSnapshot,
    workflow: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "status": plan.status.value,
        "goal": plan.goal,
        "prompt_id": plan.prompt_id,
        "prompt_version": plan.prompt_version,
        "workflow_id": workflow.workflow_id,
        "workflow_trigger_types": workflow.trigger_types,
        "workflow_required_skills": workflow.required_skills,
        "workflow_execution_mode": workflow.default_execution_mode,
        "selected_skill_ids": plan.skill_ids,
        "selected_tool_names": plan.tool_names,
        "action_count": len(plan.actions),
        "actions": [action_trace_data(action) for action in plan.actions],
        "context_snapshot_ref": plan.context_snapshot_ref,
        "context": context_trace_data(snapshot.context),
    }
    if extra:
        data.update(extra)
    return data


def context_trace_data(context: AgentContext) -> dict[str, Any]:
    return {
        "workflow_id": context.workflow_id,
        "entity_keys": sorted(context.entities),
        "entity_versions": context.entity_versions,
        "permissions": context.permissions,
        "retrieved_document_count": len(context.retrieved_documents),
        "previous_agent_output_count": len(context.previous_agent_outputs),
        "runtime_metadata_keys": sorted(context.runtime_metadata),
    }


def event_trace_data(event: BaseRuntimeEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_kind": event.kind,
        "correlation_id": event.correlation_id,
        "causality_depth": event.causality.depth,
        "idempotency_key": event.idempotency_key,
    }


def validation_trace_data(validation: ValidationResult) -> dict[str, Any]:
    return {
        "decision": validation.decision.value,
        "valid": validation.valid,
        "reason": validation.reason,
        "stale_entities": validation.stale_entities,
        "emitted_event_count": len(validation.emitted_events),
        "emitted_events": emitted_event_trace_data(validation.emitted_events),
        "suggestion": suggestion_trace_data(validation.suggestion),
    }


def tool_result_trace_data(result: ToolResult) -> dict[str, Any]:
    return {
        "result_id": result.result_id,
        "tool_name": result.tool_name,
        "success": result.success,
        "output": result.output,
        "error": result.error,
        "emitted_event_count": len(result.emitted_events),
        "emitted_events": emitted_event_trace_data(result.emitted_events),
        "suggestion": suggestion_trace_data(result.suggestion),
    }


def emitted_event_trace_data(envelopes: list[EventEnvelope]) -> list[dict[str, Any]]:
    return [
        {
            "envelope_id": envelope.envelope_id,
            **event_trace_data(envelope.event),
        }
        for envelope in envelopes
    ]


def suggestion_trace_data(suggestion: Any) -> dict[str, Any] | None:
    if suggestion is None:
        return None
    return {
        "suggestion_id": suggestion.suggestion_id,
        "title": suggestion.title,
        "target_entity_ref": suggestion.target_entity_ref,
    }
