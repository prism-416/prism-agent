from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

from src.config import Settings
from src.interfaces import Embedder, LLMClient, PromptLoader, VectorStore
from src.models import (
    AgentState,
    AnswerResponse,
    CritiqueDecision,
    QueryInput,
    RetrievalResult,
    RouteDecision,
    ToolCall,
)
from src.observability import get_logger
from src.tools.registry import ToolRegistry

T = TypeVar("T")


class RagAgentWorkflow:
    def __init__(
        self,
        *,
        settings: Settings,
        llm: LLMClient,
        embedder: Embedder,
        vector_store: VectorStore,
        prompt_loader: PromptLoader,
        tool_registry: ToolRegistry,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.embedder = embedder
        self.vector_store = vector_store
        self.prompt_loader = prompt_loader
        self.tool_registry = tool_registry
        self.logger = get_logger("rag_workflow")

    async def run(self, payload: QueryInput) -> AnswerResponse:
        trace = [AgentState.RECEIVED.value]
        self.logger.info("workflow_started", query=payload.query)

        route = await self._route(payload)
        trace.append(AgentState.ROUTED.value)
        self.logger.info("route_decision", use_retrieval=route.use_retrieval, use_tools=route.use_tools)

        filters = dict(payload.metadata_filters)
        if route.use_tools:
            tool_result = await self.tool_registry.execute(
                ToolCall(name="expand_filters", arguments={"query": payload.query})
            )
            inferred = tool_result.output.get("filters", {})
            if isinstance(inferred, dict):
                filters.update({str(k): str(v) for k, v in inferred.items()})
            trace.append(AgentState.TOOLS_EXECUTED.value)

        retrieval = await self._retrieve(payload.query, route, filters)
        trace.append(AgentState.RETRIEVED.value)
        self.logger.info("retrieval_complete", hit_count=len(retrieval))

        answer = await self._synthesize(payload.query, retrieval)
        trace.append(AgentState.SYNTHESIZED.value)

        answer = await self._maybe_retry(payload, retrieval, answer, trace)
        trace.append(AgentState.COMPLETED.value)
        self.logger.info("workflow_completed")

        citations = [item.chunk_id for item in retrieval]
        return AnswerResponse(answer=answer, citations=citations, trace=trace)

    async def _route(self, payload: QueryInput) -> RouteDecision:
        router_prompt = self.prompt_loader.render("router", {"query": payload.query})
        return await self._with_timeout(
            self.llm.generate_json(
                system_prompt=router_prompt,
                user_prompt=payload.query,
                schema=RouteDecision,
                temperature=0.0,
            )
        )

    async def _retrieve(
        self, query: str, route: RouteDecision, metadata_filters: dict[str, Any]
    ) -> list[RetrievalResult]:
        if not route.use_retrieval:
            return []

        embedding = await self._with_timeout(self.embedder.embed_query(query))
        top_k = route.top_k if route.top_k is not None else self.settings.rag_top_k
        return await self._with_timeout(
            self.vector_store.similarity_search(
                embedding=embedding,
                top_k=top_k,
                score_threshold=self.settings.rag_score_threshold,
                metadata_filters=metadata_filters,
            )
        )

    async def _synthesize(self, query: str, retrieval: list[RetrievalResult]) -> str:
        evidence = "\n".join(
            f"[{item.chunk_id}] (score={item.score:.3f}) {item.text}" for item in retrieval
        )
        system_prompt = self.prompt_loader.render(
            "synthesis", {"query": query, "evidence": evidence or "No evidence available."}
        )
        return await self._with_timeout(
            self.llm.generate_text(
                system_prompt=system_prompt, user_prompt=query, temperature=0.2
            )
        )

    async def _maybe_retry(
        self,
        payload: QueryInput,
        retrieval: list[RetrievalResult],
        answer: str,
        trace: list[str],
    ) -> str:
        if self.settings.max_workflow_retries <= 0:
            return answer

        evidence = "\n".join(f"[{item.chunk_id}] {item.text}" for item in retrieval)
        critic_prompt = self.prompt_loader.render(
            "critic",
            {"query": payload.query, "answer": answer, "evidence": evidence},
        )
        critique = await self._with_timeout(
            self.llm.generate_json(
                system_prompt=critic_prompt,
                user_prompt=answer,
                schema=CritiqueDecision,
                temperature=0.0,
            )
        )
        trace.append(AgentState.CRITIQUED.value)

        if not critique.retry:
            return answer

        wider_embedding = await self._with_timeout(self.embedder.embed_query(payload.query))
        wider = await self._with_timeout(
            self.vector_store.similarity_search(
                embedding=wider_embedding,
                top_k=max(self.settings.rag_top_k + 2, self.settings.rag_top_k),
                score_threshold=max(self.settings.rag_score_threshold - 0.1, 0.0),
                metadata_filters=payload.metadata_filters,
            )
        )
        return await self._synthesize(payload.query, wider)

    async def _with_timeout(self, awaitable: Awaitable[T]) -> T:
        return await asyncio.wait_for(awaitable, timeout=self.settings.llm_timeout_seconds)

