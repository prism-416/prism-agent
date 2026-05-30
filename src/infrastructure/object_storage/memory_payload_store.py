from __future__ import annotations

from typing import Any

from infrastructure.object_storage.base import JsonPayloadStore


class MemoryPayloadStore(JsonPayloadStore):
    def __init__(self, payloads: dict[str, dict[str, Any]] | None = None) -> None:
        self.payloads = payloads or {}

    def fetch_json(self, object_name: str, version_id: str | None = None) -> dict[str, Any]:
        _ = version_id
        if object_name not in self.payloads:
            raise KeyError(f"Payload object not found: {object_name}")
        return self.payloads[object_name]
