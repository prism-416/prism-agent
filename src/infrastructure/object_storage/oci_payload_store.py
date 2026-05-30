from __future__ import annotations

import json
from typing import Any

from infrastructure.object_storage.base import JsonPayloadStore
from infrastructure.object_storage.oci_client import build_object_storage_client


class ObjectStoragePayloadStore(JsonPayloadStore):
    def __init__(
        self,
        namespace: str | None,
        bucket_name: str | None,
        client: Any | None = None,
    ) -> None:
        if not namespace or not bucket_name:
            raise ValueError(
                "OCI payload namespace and bucket name are required for feature provisioning."
            )
        self.namespace = namespace
        self.bucket_name = bucket_name
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_object_storage_client()
        return self._client

    def fetch_json(self, object_name: str, version_id: str | None = None) -> dict[str, Any]:
        kwargs = {"version_id": version_id} if version_id else {}
        response = self.client.get_object(
            self.namespace,
            self.bucket_name,
            object_name,
            **kwargs,
        )
        raw = _response_data_to_bytes(response.data)
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError(f"Payload object must contain a JSON object: {object_name}")
        return decoded


def _response_data_to_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    content = getattr(data, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    if hasattr(data, "read"):
        value = data.read()
        return value if isinstance(value, bytes) else str(value).encode("utf-8")
    raise TypeError("Unsupported OCI object response data type.")
