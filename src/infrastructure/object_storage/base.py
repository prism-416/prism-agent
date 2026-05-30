from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class JsonPayloadStore(ABC):
    @abstractmethod
    def fetch_json(self, object_name: str, version_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError
