from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from domain.events import utc_now


class ActionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    REQUIRES_APPROVAL = "requires_approval"
    STALE = "stale"


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    action_type: str
    tool_name: str
    instruction: str
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    status: ActionStatus = ActionStatus.PENDING
    idempotency_key: str
    expected_entity_versions: dict[str, str | int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def is_unblocked(self, completed_action_ids: set[str]) -> bool:
        return all(action_id in completed_action_ids for action_id in self.depends_on)

    def with_status(self, status: ActionStatus) -> PlannedAction:
        return self.model_copy(update={"status": status, "updated_at": utc_now()})
