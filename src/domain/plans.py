from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from domain.actions import ActionStatus, PlannedAction
from domain.events import utc_now


class PlanStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    REPLAN_REQUIRED = "replan_required"


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    source_event_id: str
    workspace_id: str
    project_id: str | None
    goal: str
    prompt_id: str
    prompt_version: str
    skill_ids: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    actions: list[PlannedAction] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.PLANNED
    context_snapshot_ref: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def get_action(self, action_id: str) -> PlannedAction | None:
        return next((action for action in self.actions if action.action_id == action_id), None)

    def completed_action_ids(self) -> set[str]:
        return {
            action.action_id for action in self.actions if action.status == ActionStatus.COMPLETED
        }

    def next_pending_action(self) -> PlannedAction | None:
        completed = self.completed_action_ids()
        for action in self.actions:
            if action.status == ActionStatus.PENDING and action.is_unblocked(completed):
                return action
        return None

    def replace_action(self, updated_action: PlannedAction) -> AgentPlan:
        actions = [
            updated_action if action.action_id == updated_action.action_id else action
            for action in self.actions
        ]
        status = self.status
        if all(
            action.status in {ActionStatus.COMPLETED, ActionStatus.SKIPPED} for action in actions
        ):
            status = PlanStatus.COMPLETED
        elif any(action.status == ActionStatus.FAILED for action in actions):
            status = PlanStatus.FAILED
        elif any(action.status == ActionStatus.REQUIRES_APPROVAL for action in actions):
            status = PlanStatus.WAITING_FOR_APPROVAL
        elif any(action.status == ActionStatus.STALE for action in actions):
            status = PlanStatus.REPLAN_REQUIRED
        else:
            status = PlanStatus.RUNNING
        return self.model_copy(
            update={"actions": actions, "status": status, "updated_at": utc_now()}
        )
