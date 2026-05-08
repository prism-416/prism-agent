from __future__ import annotations

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.results import ToolResult
from domain.suggestions import AgentSuggestion


class CreateAgentSuggestionTool(BaseAgentTool):
    name = "create_agent_suggestion"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        title = str(action.input.get("title") or "Agent suggestion")
        body = str(action.input.get("body") or action.instruction)
        suggestion = AgentSuggestion(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            plan_id=action.plan_id,
            action_id=action.action_id,
            title=title,
            body=body,
            target_entity_ref=action.input.get("target_entity_ref"),
            proposed_changes=action.input.get("proposed_changes", {}),
        )
        event = DomainEvent(
            event_type="agent_suggestion.created",
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            payload={"suggestion_id": suggestion.suggestion_id},
            causality=context.source_event.event.causality.child(context.source_event.event_id),
        )
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={"suggestion_id": suggestion.suggestion_id},
            emitted_events=[EventEnvelope.wrap(event)],
            suggestion=suggestion,
        )
