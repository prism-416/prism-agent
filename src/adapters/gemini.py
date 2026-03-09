from __future__ import annotations

import asyncio
import json
import os
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from pydantic_ai import Agent
from tenacity import retry, stop_after_attempt, wait_exponential

from src.errors import LlmError
from src.interfaces import Embedder, LLMClient

TModel = TypeVar("TModel", bound=BaseModel)


class GeminiPydanticAIClient(LLMClient, Embedder):
    def __init__(self, model_name: str, embedding_model: str = "text-embedding-004") -> None:
        self.model_name = model_name
        self.embedding_model = embedding_model

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3))
    async def generate_text(
        self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2
    ) -> str:
        try:
            agent = cast(
                Agent[Any, str],
                Agent(
                    cast(Any, self.model_name),
                    result_type=str,
                    system_prompt=system_prompt,
                    model_settings={"temperature": temperature},
                ),  # type: ignore[call-overload]
            )
            result = await agent.run(user_prompt)
            return result.output
        except Exception as exc:  # pragma: no cover - provider/runtime-specific
            raise LlmError(f"Text generation failed: {exc}") from exc

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3))
    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[TModel],
        temperature: float = 0.0,
    ) -> TModel:
        try:
            agent = cast(
                Agent[Any, TModel],
                Agent(
                    cast(Any, self.model_name),
                    result_type=cast(Any, schema),
                    system_prompt=system_prompt,
                    model_settings={"temperature": temperature},
                ),  # type: ignore[call-overload]
            )
            result = await agent.run(user_prompt)
            return result.output
        except Exception as exc:  # pragma: no cover - provider/runtime-specific
            raise LlmError(f"Structured generation failed: {exc}") from exc

    async def embed_query(self, text: str) -> list[float]:
        vector = await self._embed_with_google_genai(text)
        if vector is not None:
            return vector

        payload = await self.generate_text(
            system_prompt=(
                "Return only a JSON array of floats representing an embedding vector for the text."
            ),
            user_prompt=text,
            temperature=0.0,
        )
        try:
            parsed = json.loads(payload)
            if not isinstance(parsed, list):
                raise ValueError("Embedding payload is not a list.")
            return [float(value) for value in parsed]
        except Exception as exc:
            raise LlmError(f"Embedding parse failed: {exc}") from exc

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(await self.embed_query(text))
        return vectors

    async def _embed_with_google_genai(self, text: str) -> list[float] | None:
        try:
            from google import genai
        except Exception:
            return None

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None

        def _embed_sync() -> list[float]:
            client = genai.Client(api_key=api_key)
            response = client.models.embed_content(
                model=self.embedding_model,
                contents=text,
            )
            emb = getattr(response, "embeddings", None)
            if not emb:
                raise LlmError("No embeddings returned by Gemini API.")
            values = getattr(emb[0], "values", None)
            if values is None:
                raise LlmError("Gemini embedding response missing values.")
            return [float(v) for v in values]

        return await asyncio.to_thread(_embed_sync)

