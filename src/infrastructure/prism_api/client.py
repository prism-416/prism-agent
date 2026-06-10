from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from domain.events import BaseRuntimeEvent


class PrismApiNotFoundError(RuntimeError):
    """Raised when the Prism API returns 404 for a requested resource."""


class PrismApiConflictError(RuntimeError):
    """Raised when the Prism API returns 409 (e.g. a stale pull request head SHA)."""


class PrismApiUnprocessableError(RuntimeError):
    """Raised when the Prism API returns 422 (e.g. a review comment off the PR diff)."""


class PrismApiClient:
    """Prism API boundary.

    HTTP boundary for Prizmatic API calls used by local and production runtime
    adapters.
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
            explicit_value = _payload_context_value(payload, context_key)
            if context_key == "workspace_members":
                entities[context_key] = self._workspace_members_context(
                    event.workspace_id,
                    explicit_value,
                )
                continue
            if context_key == "project_members":
                entities[context_key] = self._project_members_context(
                    event.workspace_id,
                    entities,
                    explicit_value,
                )
                continue
            if context_key == "workspace_jobs":
                entities[context_key] = self._workspace_jobs_context(
                    event.workspace_id,
                    explicit_value,
                )
                continue
            if context_key == "project_jobs":
                explicit_jobs = _extract_items(explicit_value)
                entities[context_key] = (
                    explicit_jobs
                    if explicit_jobs or explicit_value is not None
                    else _extract_items(entities.get("workspace_jobs"))
                )
                continue
            if context_key == "member_workloads":
                entities[context_key] = self._member_workloads_context(
                    event.workspace_id,
                    entities,
                    explicit_value,
                )
                continue
            if context_key == "project_work_items":
                entities[context_key] = self._project_work_items_context(event, explicit_value)
                continue
            if context_key == "pull_request_diff":
                entities[context_key] = self._pull_request_diff_context(event, explicit_value)
                continue
            entities[context_key] = (
                explicit_value
                if explicit_value is not None
                else self._default_entity(context_key, event)
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

    def get_workspace_members(self, workspace_id: str) -> list[dict[str, Any]]:
        data = self._request_json(
            "GET",
            f"/workspaces/{quote(workspace_id, safe='')}/members",
            auth_mode="bearer",
        )
        return _extract_items(data)

    def get_workspace_jobs(self, workspace_id: str) -> list[dict[str, Any]]:
        data = self._request_json(
            "GET",
            f"/workspaces/{quote(workspace_id, safe='')}/jobs",
            auth_mode="bearer",
        )
        return _extract_items(data)

    def get_workspace_member_workloads(
        self,
        workspace_id: str,
        *,
        internal: bool = True,
    ) -> list[dict[str, Any]]:
        path = f"/workspaces/{quote(workspace_id, safe='')}/member-workloads"
        if internal:
            path += "/internal"
        data = self._request_json("GET", path)
        return _extract_items(data)

    def get_workspace_member_workload(
        self,
        workspace_id: str,
        user_id: str,
        *,
        internal: bool = True,
    ) -> dict[str, Any]:
        path = f"/workspaces/{quote(workspace_id, safe='')}/member-workloads"
        if internal:
            path += "/internal"
        data = self._request_json("GET", f"{path}/{quote(user_id, safe='')}")
        if isinstance(data, dict):
            return data
        raise RuntimeError("Prism API member workload response must be an object.")

    def search_work_items(
        self,
        project_id: str,
        params: dict[str, Any] | None = None,
        *,
        internal: bool = True,
    ) -> dict[str, Any]:
        base_path = f"/projects/{quote(project_id, safe='')}/work-items"
        if internal:
            base_path += "/internal"
        query = _query_string(params or {})
        data = self._request_json("GET", f"{base_path}{query}")
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {
                "items": data,
                "total": len(data),
                "limit": len(data),
                "offset": 0,
            }
        return {"items": [], "total": 0, "limit": 0, "offset": 0}

    def find_similar_work_items(
        self,
        project_id: str,
        embedding: list[float],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        data = self._request_json(
            "POST",
            f"/projects/{quote(project_id, safe='')}/work-items/internal/similar",
            {"embedding": embedding, "limit": limit},
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

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

    def get_pull_request(
        self,
        project_id: str,
        pull_number: str | int,
        *,
        include_diff: bool = True,
        include_files: bool = True,
    ) -> dict[str, Any]:
        query = _query_string(
            {
                "includeDiff": "true" if include_diff else "false",
                "includeFiles": "true" if include_files else "false",
            }
        )
        return self._request_json(
            "GET",
            f"/projects/{quote(project_id, safe='')}/pull-requests/internal/"
            f"{quote(str(pull_number), safe='')}{query}",
        )

    def create_pull_request_review(
        self,
        project_id: str,
        pull_number: str | int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/projects/{quote(project_id, safe='')}/pull-requests/internal/"
            f"{quote(str(pull_number), safe='')}/reviews",
            payload,
        )

    def create_agent_run(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-runs/internal",
            payload,
        )

    def get_agent_run_state(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        data = self._request_json(
            "GET",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-runs/internal/"
            f"{quote(run_id, safe='')}/state",
        )
        if not isinstance(data, dict):
            raise RuntimeError("Prism API agent run state response must be an object.")
        return data

    def update_agent_run_status(
        self,
        workspace_id: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "PATCH",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-runs/internal/"
            f"{quote(run_id, safe='')}/status",
            payload,
        )

    def upsert_agent_run_step(
        self,
        workspace_id: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-runs/internal/"
            f"{quote(run_id, safe='')}/steps",
            payload,
        )

    def upsert_agent_action(
        self,
        workspace_id: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-runs/internal/"
            f"{quote(run_id, safe='')}/actions",
            payload,
        )

    def create_agent_action_event(
        self,
        workspace_id: str,
        action_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-actions/internal/"
            f"{quote(action_id, safe='')}/events",
            payload,
        )

    def upsert_agent_memory(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/agent-memories",
            payload,
            auth_mode="bearer",
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth_mode: str = "internal",
    ) -> Any:
        if not self.base_url:
            raise RuntimeError("PRISM_API_BASE_URL is required for live Prism API calls.")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.token and auth_mode in {"internal", "both"}:
            headers["x-internal-api-token"] = self.token
        if self.token and auth_mode in {"bearer", "both"}:
            headers["Authorization"] = f"Bearer {self.token}"
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
            request_payload = _format_request_payload(payload)
            if exc.code == 404:
                raise PrismApiNotFoundError(
                    f"Prism API {method} {path} returned 404: {detail}{request_payload}"
                ) from exc
            if exc.code == 409:
                raise PrismApiConflictError(
                    f"Prism API {method} {path} returned 409: {detail}{request_payload}"
                ) from exc
            if exc.code == 422:
                raise PrismApiUnprocessableError(
                    f"Prism API {method} {path} returned 422: {detail}{request_payload}"
                ) from exc
            raise RuntimeError(
                f"Prism API {method} {path} failed: {exc.code} {detail}{request_payload}"
            ) from exc
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

    def _pull_request_diff_context(
        self,
        event: BaseRuntimeEvent,
        explicit_value: Any,
    ) -> dict[str, Any] | None:
        if explicit_value is not None:
            return explicit_value
        pull_number = _pull_request_number(event.payload)
        if not (self.is_configured and event.project_id and pull_number is not None):
            return None
        try:
            return self.get_pull_request(event.project_id, pull_number)
        except PrismApiNotFoundError:
            return None

    def _workspace_members_context(
        self,
        workspace_id: str,
        explicit_value: Any,
    ) -> list[dict[str, Any]]:
        _ = workspace_id
        explicit_members = _extract_items(explicit_value)
        if explicit_members or explicit_value is not None:
            return explicit_members
        return _extract_items(explicit_value)

    def _project_members_context(
        self,
        workspace_id: str,
        entities: dict[str, Any],
        explicit_value: Any,
    ) -> list[dict[str, Any]]:
        explicit_members = _extract_items(explicit_value)
        if explicit_members:
            return explicit_members
        workspace_members = entities.get("workspace_members")
        if isinstance(workspace_members, list):
            return workspace_members
        return self._workspace_members_context(workspace_id, explicit_value)

    def _workspace_jobs_context(
        self,
        workspace_id: str,
        explicit_value: Any,
    ) -> list[dict[str, Any]]:
        _ = workspace_id
        explicit_jobs = _extract_items(explicit_value)
        if explicit_jobs or explicit_value is not None:
            return explicit_jobs
        return _extract_items(explicit_value)

    def _project_work_items_context(
        self,
        event: BaseRuntimeEvent,
        explicit_value: Any,
    ) -> list[dict[str, Any]]:
        """Existing project work items, slimmed for planning context.

        Descriptions are dropped and the list is capped so a large backlog cannot
        blow up the planning prompt; titles and hierarchy are what duplicate
        avoidance and attachment decisions need.
        """
        explicit_items = _extract_items(explicit_value)
        if explicit_items or not self.is_configured or not event.project_id:
            return explicit_items
        try:
            result = self.search_work_items(
                event.project_id,
                {"limit": PROJECT_WORK_ITEMS_CONTEXT_LIMIT},
            )
        except RuntimeError:
            return []
        items = result.get("items")
        if not isinstance(items, list):
            return []
        return [_slim_work_item(item) for item in items if isinstance(item, dict)]

    def _member_workloads_context(
        self,
        workspace_id: str,
        entities: dict[str, Any],
        explicit_value: Any,
    ) -> list[dict[str, Any]]:
        explicit_workloads = _extract_items(explicit_value)
        if explicit_workloads or not self.is_configured:
            return explicit_workloads

        try:
            workloads = self.get_workspace_member_workloads(workspace_id)
            if workloads:
                return workloads
        except RuntimeError as exc:
            return self._unavailable_member_workloads(entities, exc)

        return []

    @staticmethod
    def _unavailable_member_workloads(
        entities: dict[str, Any],
        exc: RuntimeError,
    ) -> list[dict[str, Any]]:
        members = entities.get("project_members") or entities.get("workspace_members") or []
        if not isinstance(members, list):
            return []
        return [
            PrismApiClient._unavailable_member_workload(member, exc)
            for member in members
            if isinstance(member, dict)
        ]

    @staticmethod
    def _unavailable_member_workload(
        member: dict[str, Any],
        exc: RuntimeError,
    ) -> dict[str, Any]:
        return {
            "userId": member.get("userId"),
            "fullName": member.get("fullName"),
            "username": member.get("username"),
            "role": member.get("role"),
            "jobIds": member.get("jobIds", []),
            "jobNames": member.get("jobNames", []),
            "assignedItemCount": None,
            "activeItemCount": None,
            "todoItemCount": None,
            "inProgressItemCount": None,
            "inReviewItemCount": None,
            "doneItemCount": None,
            "archivedItemCount": None,
            "overdueItemCount": None,
            "dueTodayItemCount": None,
            "dueThisWeekItemCount": None,
            "source": "unavailable",
            "limitations": (
                "The current API exposes workspace member workload counts but no "
                "capacity, availability, estimate, or project-breakdown fields."
            ),
            "error": str(exc)[:500],
        }


def _format_request_payload(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return ""
    return f" request_payload={json.dumps(payload, sort_keys=True)}"


def _pull_request_number(payload: dict[str, Any]) -> str | int | None:
    for key in ("pullNumber", "pull_number"):
        value = payload.get(key)
        if value is not None:
            return value
    pull_request = payload.get("pull_request") or payload.get("pullRequest")
    if isinstance(pull_request, dict):
        for key in ("number", "pullNumber", "pull_number"):
            value = pull_request.get(key)
            if value is not None:
                return value
    return None


PROJECT_WORK_ITEMS_CONTEXT_LIMIT = 100
_WORK_ITEM_CONTEXT_FIELDS = (
    "itemId",
    "parentId",
    "title",
    "status",
    "priority",
    "dueDate",
    "assigneeUsernames",
    "labelNames",
)


def _slim_work_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        field: item[field] for field in _WORK_ITEM_CONTEXT_FIELDS if item.get(field) is not None
    }


def _payload_context_value(payload: dict[str, Any], context_key: str) -> Any:
    if context_key in payload:
        return payload[context_key]
    camel_key = _snake_to_camel(context_key)
    if camel_key in payload:
        return payload[camel_key]
    return None


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "members", "jobs", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _query_string(params: dict[str, Any]) -> str:
    filtered = {
        key: value
        for key, value in params.items()
        if value is not None and value != "" and value != []
    }
    if not filtered:
        return ""
    return "?" + urlencode(filtered, doseq=True)
