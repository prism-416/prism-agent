from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from src.models import Chunk, RetrievalResult, ToolCall, ToolResult

TModel = TypeVar("TModel", bound=BaseModel)


class PromptLoader(Protocol):
    def render(self, prompt_name: str, variables: dict[str, Any] | None = None) -> str: ...


class LLMClient(Protocol):
    async def generate_text(
        self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2
    ) -> str: ...

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[TModel],
        temperature: float = 0.0,
    ) -> TModel: ...


class Embedder(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    async def upsert_chunks(self, chunks: list[Chunk]) -> None: ...
    async def similarity_search(
        self,
        *,
        embedding: list[float],
        top_k: int,
        score_threshold: float,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]: ...


class Tool(Protocol):
    name: str
    description: str

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]: ...


class ToolExecutor(Protocol):
    async def execute(self, tool_call: ToolCall) -> ToolResult: ...

