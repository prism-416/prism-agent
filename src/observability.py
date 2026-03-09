from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import cast

import structlog

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
_configured = False


def bind_request_id(request_id: str | None = None) -> str:
    value = request_id or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=value)
    request_id_ctx.set(value)
    return value


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))

