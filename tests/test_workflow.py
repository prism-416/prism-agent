from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.models import QueryInput, RetrievalResult
from src.tools.registry import ToolRegistry
from src.workflows.rag_agent import RagAgentWorkflow


class FakePromptLoader:
    def render(self, prompt_name: str, variables: dict[str, object] | None = None) -> str:
        _ = variables
        return f"prompt:{prompt_name}"


class FakeLLM:
    async def generate_text(
        self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2
    ) -> str:
        _ = (system_prompt, user_prompt, temperature)
        return "final answer"

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[object],
        temperature: float = 0.0,
    ) -> object:
        _ = (system_prompt, user_prompt, temperature)
        name = getattr(schema, "__name__", "")
        if name == "RouteDecision":
            return schema(use_retrieval=True, use_tools=False, top_k=2)
        if name == "CritiqueDecision":
            return schema(retry=False, reason="ok")
        raise ValueError("Unsupported schema")


class FakeEmbedder:
    async def embed_query(self, text: str) -> list[float]:
        _ = text
        return [0.1, 0.2, 0.3]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        _ = texts
        return [[0.1, 0.2, 0.3]]


class FakeVectorStore:
    async def upsert_chunks(self, chunks: list[object]) -> None:
        _ = chunks

    async def similarity_search(
        self,
        *,
        embedding: list[float],
        top_k: int,
        score_threshold: float,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[RetrievalResult]:
        _ = (embedding, top_k, score_threshold, metadata_filters)
        return [
            RetrievalResult(
                chunk_id="doc1:0",
                doc_id="doc1",
                text="evidence text",
                score=0.9,
                metadata={},
            )
        ]


def _settings() -> Settings:
    return Settings(
        GOOGLE_API_KEY="dummy",
        PG_DSN="postgresql://localhost:5432/rag",
        PROMPTS_DIR=Path("prompts"),
        MAX_WORKFLOW_RETRIES=1,
        LLM_TIMEOUT_SECONDS=2.0,
    )


async def test_workflow_happy_path() -> None:
    registry = ToolRegistry()
    workflow = RagAgentWorkflow(
        settings=_settings(),
        llm=FakeLLM(),  # type: ignore[arg-type]
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeVectorStore(),  # type: ignore[arg-type]
        prompt_loader=FakePromptLoader(),
        tool_registry=registry,
    )

    result = await workflow.run(QueryInput(query="What is this?"))
    assert result.answer == "final answer"
    assert result.citations == ["doc1:0"]
    assert "completed" in result.trace

