from __future__ import annotations

import asyncpg
from tenacity import retry, stop_after_attempt, wait_exponential

from src.errors import RetrievalError
from src.models import Chunk, RetrievalResult


class PgvectorStore:
    def __init__(self, dsn: str, embedding_dimension: int) -> None:
        self._dsn = dsn
        self._embedding_dimension = embedding_dimension
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ensure_schema(self) -> None:
        await self.connect()
        if self._pool is None:
            raise RetrievalError("Pool not initialized.")
        query = f"""
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS rag_chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            embedding VECTOR({self._embedding_dimension}) NOT NULL
        );
        """
        async with self._pool.acquire() as conn:
            await conn.execute(query)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3))
    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        await self.connect()
        if self._pool is None:
            raise RetrievalError("Pool not initialized.")
        sql = """
        INSERT INTO rag_chunks (chunk_id, doc_id, content, metadata, embedding)
        VALUES ($1, $2, $3, $4::jsonb, $5::vector)
        ON CONFLICT (chunk_id)
        DO UPDATE SET
            doc_id = EXCLUDED.doc_id,
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            embedding = EXCLUDED.embedding;
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for chunk in chunks:
                    if chunk.embedding is None:
                        raise RetrievalError(f"Chunk {chunk.chunk_id} missing embedding.")
                    await conn.execute(
                        sql,
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.text,
                        chunk.metadata,
                        chunk.embedding,
                    )

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3))
    async def similarity_search(
        self,
        *,
        embedding: list[float],
        top_k: int,
        score_threshold: float,
        metadata_filters: dict[str, str] | None = None,
    ) -> list[RetrievalResult]:
        await self.connect()
        if self._pool is None:
            raise RetrievalError("Pool not initialized.")

        metadata_filters = metadata_filters or {}
        sql = """
        SELECT
          chunk_id,
          doc_id,
          content,
          metadata,
          1 - (embedding <=> $1::vector) AS score
        FROM rag_chunks
        WHERE (metadata @> $2::jsonb)
          AND (1 - (embedding <=> $1::vector)) >= $3
        ORDER BY embedding <=> $1::vector
        LIMIT $4;
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, embedding, metadata_filters, score_threshold, top_k)

        return [
            RetrievalResult(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                text=row["content"],
                score=float(row["score"]),
                metadata=dict(row["metadata"]),
            )
            for row in rows
        ]

