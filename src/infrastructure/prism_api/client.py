from __future__ import annotations

from typing import Any

from domain.events import BaseRuntimeEvent


class PrismApiClient:
    """Prism API boundary.

    The local implementation is intentionally deterministic. Production can
    replace method bodies with HTTP calls without changing application services.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url
        self.token = token
        self._versions: dict[str, str | int] = {}

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

    @staticmethod
    def _default_entity(context_key: str, event: BaseRuntimeEvent) -> dict[str, Any]:
        return {
            "id": event.payload.get(f"{context_key}_id", f"{context_key}-unknown"),
            "type": context_key,
            "version": event.payload.get("version", 1),
        }
