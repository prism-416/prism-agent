from __future__ import annotations

from typing import Any

from src.adapters.gemini import GeminiPydanticAIClient
from src.adapters.pgvector_store import PgvectorStore
from src.config import get_settings
from src.models import Chunk, Document
from src.observability import bind_request_id, configure_logging, get_logger


def _chunk_document(document: Document, chunk_size: int = 800) -> list[Chunk]:
    chunks: list[Chunk] = []
    text = document.text
    for idx in range(0, len(text), chunk_size):
        piece = text[idx : idx + chunk_size]
        chunks.append(
            Chunk(
                chunk_id=f"{document.doc_id}:{idx // chunk_size}",
                doc_id=document.doc_id,
                text=piece,
                metadata=document.metadata,
            )
        )
    return chunks


async def handle_index(event: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("index_handler")
    request_id = bind_request_id(event.get("request_id"))

    model = GeminiPydanticAIClient(model_name=settings.gemini_model)
    vector_store = PgvectorStore(settings.pg_dsn, settings.embedding_dimension)

    document = Document(
        doc_id=str(event["doc_id"]),
        source=str(event.get("source", "unknown")),
        text=str(event["text"]),
        metadata=dict(event.get("metadata", {})),
    )
    chunks = _chunk_document(document)
    logger.info("index_started", doc_id=document.doc_id, chunk_count=len(chunks))
    embeddings = await model.embed_documents([item.text for item in chunks])
    for idx, embedding in enumerate(embeddings):
        chunks[idx].embedding = embedding

    await vector_store.ensure_schema()
    try:
        await vector_store.upsert_chunks(chunks)
        logger.info("index_completed", doc_id=document.doc_id, chunk_count=len(chunks))
        return {"request_id": request_id, "status": "ok", "chunks_indexed": len(chunks)}
    finally:
        await vector_store.close()

