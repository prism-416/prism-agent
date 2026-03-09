from __future__ import annotations

from typing import Any

from src.bootstrap import build_workflow
from src.config import get_settings
from src.models import QueryInput
from src.observability import bind_request_id, configure_logging, get_logger


async def handle_query(event: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("query_handler")
    request_id = bind_request_id(event.get("request_id"))
    workflow, vector_store = build_workflow(settings)

    payload = QueryInput(
        query=str(event["query"]),
        session_id=event.get("session_id"),
        metadata_filters=dict(event.get("metadata_filters", {})),
    )

    await vector_store.ensure_schema()
    try:
        logger.info("query_received", session_id=payload.session_id)
        result = await workflow.run(payload)
        logger.info("query_completed", citation_count=len(result.citations))
        return {"request_id": request_id, "answer": result.model_dump()}
    finally:
        await vector_store.close()

