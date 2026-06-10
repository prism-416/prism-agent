from __future__ import annotations

import json
import logging
import sys
from typing import Any

LOGGER_NAME = "prism_agent"

_handler_attached = False


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the ``prism_agent`` logger to emit single-line records to stdout.

    OCI Functions ships container stdout to OCI Logging, so structured lines here
    become queryable function logs. Idempotent: the stdout handler is attached once
    even though the function reuses the same process across invocations.
    """
    global _handler_attached
    logger = logging.getLogger(LOGGER_NAME)
    if not _handler_attached:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        _handler_attached = True
    logger.setLevel(level.upper())
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_json(logger: logging.Logger, level: int, record: dict[str, Any], **kwargs: Any) -> None:
    """Emit one JSON log line. Never raises — observability must not affect flow."""
    try:
        logger.log(level, json.dumps(record, default=str, ensure_ascii=False), **kwargs)
    except Exception:
        return
