from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from domain.events import utc_now


class AgentSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    project_id: str | None = None
    plan_id: str
    action_id: str
    title: str
    body: str
    target_entity_ref: str | None = None
    proposed_changes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
