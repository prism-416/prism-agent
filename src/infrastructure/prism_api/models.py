from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PrismEntity(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity_ref: str
    entity_type: str
    version: str | int
    data: dict[str, Any] = Field(default_factory=dict)
