from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from domain.events import BaseRuntimeEvent

ALLOWED_MANUAL_TRIGGERS = {
    "decompose_story",
    "improve_workitem_description",
    "generate_acceptance_criteria",
    "plan_sprint",
    "summarize_project",
    "refine_backlog",
    "explain_project_risk",
}

ALLOWED_DOMAIN_TRIGGERS = {
    "epic.created",
    "epic.description.updated",
    "feature.provisioning.requested",
    "story.created",
    "story.ready_for_breakdown",
    "sprint.started",
    "sprint.ended",
    "pr.opened",
    "pr.merged",
}

ALLOWED_SCHEDULED_TRIGGERS = {
    "daily_summary",
    "sprint_planning",
    "sprint_midpoint_risk_check",
    "sprint_end_risk_check",
    "sprint_report_generation",
    "stale_work_scan",
}

IGNORED_LOW_VALUE_TRIGGERS = {
    "task.updated",
    "comment.created",
    "field.updated",
    "artifact.updated",
    "status.changed.minor",
}

TERMINAL_EVENT_TYPES = {
    "agent_suggestion.created",
    "insight.created",
    "agent.completed",
    "agent.failed",
}


class TriggerPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    trigger_key: str
    reason: str


class PMTriggerPolicy:
    """Pure trigger-scope policy for PM Agent activation."""

    def evaluate(self, event: BaseRuntimeEvent) -> TriggerPolicyDecision:
        trigger_key = trigger_key_for(event)
        event_type = event.event_type

        if event_type in IGNORED_LOW_VALUE_TRIGGERS:
            return TriggerPolicyDecision(
                allowed=False,
                trigger_key=trigger_key,
                reason="ignored_low_value_event",
            )

        if event_type in TERMINAL_EVENT_TYPES:
            return TriggerPolicyDecision(
                allowed=False,
                trigger_key=trigger_key,
                reason="terminal_event",
            )

        allowed = False
        if event.kind == "manual":
            allowed = event_type in ALLOWED_MANUAL_TRIGGERS
        elif event.kind == "domain":
            allowed = event_type in ALLOWED_DOMAIN_TRIGGERS
        elif event.kind == "scheduled":
            allowed = event_type in ALLOWED_SCHEDULED_TRIGGERS
        elif event.kind == "agent_action":
            allowed = True

        return TriggerPolicyDecision(
            allowed=allowed,
            trigger_key=trigger_key,
            reason="allowed" if allowed else "unsupported_event",
        )


def trigger_key_for(event: BaseRuntimeEvent) -> str:
    if event.kind == "agent_action":
        return "agent.action.requested"
    return f"{event.kind}.{event.event_type}"


class ApprovalMode(StrEnum):
    AUTO_COMMIT = "auto_commit"
    USER_APPROVAL_REQUIRED = "user_approval_required"
    SUGGEST_ONLY = "suggest_only"


class ActionApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_mode: ApprovalMode = ApprovalMode.SUGGEST_ONLY
    tool_modes: dict[str, ApprovalMode] = Field(default_factory=dict)

    def requires_approval(self, tool_name: str) -> bool:
        mode = self.tool_modes.get(tool_name, self.default_mode)
        return mode in {ApprovalMode.USER_APPROVAL_REQUIRED, ApprovalMode.SUGGEST_ONLY}

    def should_auto_commit(self, tool_name: str) -> bool:
        return self.tool_modes.get(tool_name, self.default_mode) == ApprovalMode.AUTO_COMMIT


@dataclass(frozen=True)
class RecursionLimitPolicy:
    max_depth: int

    def allows(self, depth: int) -> bool:
        return depth <= self.max_depth


class StaleContextPolicy:
    def diff(
        self,
        expected_versions: dict[str, str | int],
        current_versions: dict[str, str | int],
    ) -> dict[str, dict[str, str | int | None]]:
        stale: dict[str, dict[str, str | int | None]] = {}
        for entity_ref, expected in expected_versions.items():
            current = current_versions.get(entity_ref)
            if current != expected:
                stale[entity_ref] = {"expected": expected, "current": current}
        return stale


class IdempotencyPolicy:
    @staticmethod
    def key_for(plan_id: str, action_id: str, tool_name: str) -> str:
        return f"{plan_id}:{action_id}:{tool_name}"
