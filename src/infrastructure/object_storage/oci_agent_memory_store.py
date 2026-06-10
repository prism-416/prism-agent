from __future__ import annotations

import json
from typing import Any

from infrastructure.object_storage.oci_client import build_object_storage_client
from infrastructure.object_storage.oci_payload_store import _response_data_to_bytes


class ObjectStorageAgentMemoryStore:
    """Durable agent-state memory transport backed by OCI Object Storage.

    Each memory record is written as a JSON object at
    ``{prefix}/{workspace_id}/{run_id}/{memoryId}.json``. Deterministic memory ids
    mean updates overwrite in place, and a run's records can be listed by prefix and
    replayed in a later invocation. This uses the function's own OCI credentials, so
    it needs no Prism API write scope.
    """

    def __init__(
        self,
        namespace: str | None,
        bucket_name: str | None,
        *,
        prefix: str = "agent-state",
        client: Any | None = None,
    ) -> None:
        if not namespace or not bucket_name:
            raise ValueError(
                "OCI namespace and bucket name are required for agent-state persistence."
            )
        self.namespace = namespace
        self.bucket_name = bucket_name
        self.prefix = prefix.strip("/")
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_object_storage_client()
        return self._client

    def put_memory(self, workspace_id: str, run_id: str, payload: dict[str, Any]) -> None:
        memory_id = str(payload.get("memoryId") or "")
        if not memory_id:
            raise ValueError("Agent memory payload requires a memoryId.")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.client.put_object(
            self.namespace,
            self.bucket_name,
            self._object_name(workspace_id, run_id, memory_id),
            body,
        )

    def fetch_run_state(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        # Object Storage holds only the agent's own records; action-status comes from
        # the persisted plan itself (re-saved on every transition), so no "actions".
        return {"memories": self._list_memories(workspace_id, run_id)}

    def _list_memories(self, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
        response = self.client.list_objects(
            self.namespace,
            self.bucket_name,
            prefix=self._run_prefix(workspace_id, run_id),
        )
        memories: list[dict[str, Any]] = []
        for summary in getattr(response.data, "objects", []) or []:
            obj = self.client.get_object(self.namespace, self.bucket_name, summary.name)
            raw = _response_data_to_bytes(obj.data)
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                memories.append(decoded)
        return memories

    def _object_name(self, workspace_id: str, run_id: str, memory_id: str) -> str:
        return f"{self._run_prefix(workspace_id, run_id)}{memory_id}.json"

    def _run_prefix(self, workspace_id: str, run_id: str) -> str:
        return f"{self.prefix}/{workspace_id}/{run_id}/"
