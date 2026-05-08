from __future__ import annotations

from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.policies import StaleContextPolicy
from domain.results import ToolResult, ValidationDecision, ValidationResult
from domain.suggestions import AgentSuggestion
from infrastructure.prism_api.client import PrismApiClient


class Validator:
    def __init__(
        self, prism_client: PrismApiClient, stale_context_policy: StaleContextPolicy | None = None
    ) -> None:
        self.prism_client = prism_client
        self.stale_context_policy = stale_context_policy or StaleContextPolicy()

    def validate_before_execution(
        self, action: PlannedAction, context: AgentContext
    ) -> ValidationResult:
        if not context.permissions.get(action.tool_name, True):
            return ValidationResult(
                decision=ValidationDecision.FAIL,
                valid=False,
                reason=f"permission_denied:{action.tool_name}",
            )

        current_versions = self.prism_client.get_current_entity_versions(
            context.workspace_id,
            context.project_id,
            list(action.expected_entity_versions),
        )
        stale = self.stale_context_policy.diff(action.expected_entity_versions, current_versions)
        if stale:
            return ValidationResult(
                decision=ValidationDecision.REPLAN,
                valid=False,
                reason="stale_context",
                stale_entities=stale,
            )

        if action.requires_approval:
            suggestion = self._approval_suggestion(action, context)
            event = self._suggestion_event(suggestion, context)
            return ValidationResult(
                decision=ValidationDecision.SUGGEST,
                valid=True,
                reason="approval_required",
                emitted_events=[EventEnvelope.wrap(event)],
                suggestion=suggestion,
            )

        return ValidationResult(decision=ValidationDecision.COMMIT, valid=True)

    def validate_after_execution(
        self,
        action: PlannedAction,
        context: AgentContext,
        result: ToolResult,
    ) -> ValidationResult:
        _ = context
        if not result.success:
            return ValidationResult(
                decision=ValidationDecision.RETRY,
                valid=False,
                reason=result.error or "tool_execution_failed",
            )
        return ValidationResult(
            decision=ValidationDecision.COMMIT,
            valid=True,
            emitted_events=result.emitted_events,
            suggestion=result.suggestion,
        )

    def approval_result(
        self,
        action: PlannedAction,
        context: AgentContext,
        validation: ValidationResult,
    ) -> ToolResult:
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=action.tool_name,
            success=True,
            output={
                "decision": validation.decision.value,
                "reason": validation.reason,
                "requires_approval": True,
                "original_input": action.input,
            },
            emitted_events=validation.emitted_events,
            suggestion=validation.suggestion,
        )

    @staticmethod
    def _approval_suggestion(action: PlannedAction, context: AgentContext) -> AgentSuggestion:
        return AgentSuggestion(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            plan_id=action.plan_id,
            action_id=action.action_id,
            title=f"Approval required: {action.tool_name}",
            body=action.instruction,
            target_entity_ref=next(iter(action.expected_entity_versions), None),
            proposed_changes={
                "tool_name": action.tool_name,
                "action_type": action.action_type,
                "input": action.input,
            },
        )

    @staticmethod
    def _suggestion_event(suggestion: AgentSuggestion, context: AgentContext) -> DomainEvent:
        return DomainEvent(
            event_type="agent_suggestion.created",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"suggestion_id": suggestion.suggestion_id},
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
