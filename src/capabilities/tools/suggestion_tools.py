from __future__ import annotations

import json
from typing import Any

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.results import ToolResult
from domain.suggestions import AgentSuggestion


class CreateAgentSuggestionTool(BaseAgentTool):
    name = "create_agent_suggestion"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        title = str(action.input.get("title") or "Agent suggestion")
        body = str(action.input.get("body") or action.instruction)
        target_entity_ref = action.input.get("target_entity_ref")
        suggestion = AgentSuggestion(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            plan_id=action.plan_id,
            action_id=action.action_id,
            title=title,
            body=body,
            target_entity_ref=str(target_entity_ref) if target_entity_ref else None,
            proposed_changes=_coerce_proposed_changes(action.input.get("proposed_changes")),
        )
        output = {"suggestion_id": suggestion.suggestion_id}
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
            suggestion=suggestion,
        )


def _coerce_proposed_changes(raw: Any) -> dict[str, Any]:
    """Accept the shapes models actually emit for an untyped object field.

    The schema cannot constrain ``input``, so proposed_changes arrives as a
    prose string or a list at least as often as a dict; a suggestion must not
    fail validation over its packaging.
    """
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError:
            return {"summary": raw}
        raw = decoded
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {"changes": raw}
    if raw is None:
        return {}
    return {"summary": str(raw)}
