from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentState(StrEnum):
    RECEIVED = "received"
    ROUTED = "routed"
    RETRIEVED = "retrieved"
    TOOLS_EXECUTED = "tools_executed"
    SYNTHESIZED = "synthesized"
    CRITIQUED = "critiqued"
    COMPLETED = "completed"


class Document(BaseModel):
    doc_id: str
    source: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    name: str
    output: dict[str, Any]


class RouteDecision(BaseModel):
    use_retrieval: bool = True
    use_tools: bool = False
    top_k: int | None = None


class CritiqueDecision(BaseModel):
    retry: bool = False
    reason: str = ""


class QueryInput(BaseModel):
    query: str
    session_id: str | None = None
    metadata_filters: dict[str, Any] = Field(default_factory=dict)


class AnswerResponse(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)
    state: AgentState = AgentState.COMPLETED
    trace: list[str] = Field(default_factory=list)

