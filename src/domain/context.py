from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from domain.events import EventEnvelope, utc_now


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    project_id: str | None
    source_event: EventEnvelope
    workflow_id: str
    entities: dict[str, Any] = Field(default_factory=dict)
    retrieved_documents: list[dict[str, Any]] = Field(default_factory=list)
    permissions: dict[str, bool] = Field(default_factory=dict)
    entity_versions: dict[str, str | int] = Field(default_factory=dict)
    previous_agent_outputs: list[dict[str, Any]] = Field(default_factory=list)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)


class ContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str | None = None
    workspace_id: str
    project_id: str | None = None
    workflow_id: str
    context: AgentContext
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def ref(self) -> str:
        return self.snapshot_id
