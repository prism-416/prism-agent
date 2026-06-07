from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from domain.events import BaseRuntimeEvent


class PrismApiNotFoundError(RuntimeError):
    """Raised when the Prism API returns 404 for a requested resource."""


class PrismApiClient:
    """Prism API boundary.

    The local implementation is intentionally deterministic. Production can
    replace method bodies with HTTP calls without changing application services.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self._versions: dict[str, str | int] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url)

    def fetch_context_entities(
        self,
        event: BaseRuntimeEvent,
        required_context: list[str],
    ) -> dict[str, Any]:
        payload = event.payload
        entities: dict[str, Any] = {}
        for context_key in required_context:
            entities[context_key] = payload.get(
                context_key, self._default_entity(context_key, event)
            )
        return entities

    def fetch_permissions(self, event: BaseRuntimeEvent) -> dict[str, bool]:
        permissions = event.payload.get("permissions")
        if isinstance(permissions, dict):
            return {str(key): bool(value) for key, value in permissions.items()}
        return {
            "create_workitem": True,
            "update_workitem": True,
            "create_agent_suggestion": True,
            "create_dashboard_insight": True,
            "generate_sprint_report": True,
        }

    def capture_entity_versions(
        self, event: BaseRuntimeEvent, entities: dict[str, Any]
    ) -> dict[str, str | int]:
        explicit_versions = event.payload.get("entity_versions")
        if isinstance(explicit_versions, dict):
            versions = {str(key): value for key, value in explicit_versions.items()}
        else:
            versions = {}
            for entity_type, entity in entities.items():
                if isinstance(entity, dict):
                    entity_id = entity.get("id") or entity.get(f"{entity_type}_id")
                    version = entity.get("version", 1)
                    if entity_id:
                        versions[f"{entity_type}:{entity_id}"] = version
        for entity_ref, version in versions.items():
            self._versions.setdefault(entity_ref, version)
        return versions

    def get_current_entity_versions(
        self,
        workspace_id: str,
        project_id: str | None,
        entity_refs: list[str],
    ) -> dict[str, str | int]:
        _ = (workspace_id, project_id)
        return {entity_ref: self._versions.get(entity_ref, "missing") for entity_ref in entity_refs}

    def set_entity_version(self, entity_ref: str, version: str | int) -> None:
        self._versions[entity_ref] = version

    def create_sprint(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/sprints/internal",
            payload,
        )

    def create_work_item(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/projects/{quote(project_id, safe='')}/work-items/internal",
            payload,
        )

    def update_work_item(
        self,
        project_id: str,
        item_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "PATCH",
            f"/projects/{quote(project_id, safe='')}/work-items/internal/{quote(item_id, safe='')}",
            payload,
        )

    def get_agent_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-runs/{quote(run_id, safe='')}",
        )

    def get_agent_run_actions(self, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
        data = self._request_json(
            "GET",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-runs/"
            f"{quote(run_id, safe='')}/actions",
        )
        if not isinstance(data, list):
            raise RuntimeError("Prism API agent run actions response must be a list.")
        return [item for item in data if isinstance(item, dict)]

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not self.base_url:
            raise RuntimeError("PRISM_API_BASE_URL is required for live Prism API calls.")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["x-internal-api-token"] = self.token
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                raise PrismApiNotFoundError(
                    f"Prism API {method} {path} returned 404: {detail}"
                ) from exc
            raise RuntimeError(f"Prism API {method} {path} failed: {exc.code} {detail}") from exc
        if not raw:
            return {}
        decoded = json.loads(raw)
        if isinstance(decoded, dict) and "data" in decoded:
            return decoded["data"]
        if isinstance(decoded, dict):
            return decoded
        raise RuntimeError(f"Prism API {method} {path} returned unsupported JSON.")

    @staticmethod
    def _default_entity(context_key: str, event: BaseRuntimeEvent) -> dict[str, Any]:
        return {
            "id": event.payload.get(f"{context_key}_id", f"{context_key}-unknown"),
            "type": context_key,
            "version": event.payload.get("version", 1),
        }
