from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from domain.backlog import compute_backlog_signals
from domain.events import BaseRuntimeEvent
from infrastructure.observability.logging_config import get_logger, log_json


class PrismApiBadRequestError(RuntimeError):
    """Raised when the Prism API returns 400 (a malformed or invalid request body)."""


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
            if context_key == "backlog_work_items":
                entities[context_key] = self._backlog_work_items_context(event, explicit_value)
                continue
            if context_key in {"sprints", "recent_sprints"}:
                entities[context_key] = self._sprints_context(event, explicit_value)
                continue
            if context_key == "backlog_signals":
                # Computed after the loop so it can see backlog_work_items and
                # member_workloads regardless of declaration order.
                continue
            if context_key == "pull_request_diff":
                entities[context_key] = self._pull_request_diff_context(event, explicit_value)
                continue
            if context_key == "pull_request_event":
                entities[context_key] = self._pull_request_event_context(event, explicit_value)
                continue
            entities[context_key] = (
                explicit_value
                if explicit_value is not None
                else self._default_entity(context_key, event)
            )
        if "backlog_signals" in required_context:
            explicit_signals = _payload_context_value(payload, "backlog_signals")
            entities["backlog_signals"] = (
                explicit_signals
                if isinstance(explicit_signals, dict)
                else compute_backlog_signals(
                    _extract_items(entities.get("backlog_work_items")),
                    _extract_items(entities.get("member_workloads")),
                )
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
        # Versions are tracked per-process; refs never observed in this process
        # are omitted rather than reported with a sentinel, because "unobserved"
        # is not evidence the entity changed. Actions usually execute in a later
        # invocation than the one that planned them, so a sentinel here would
        # mark every cross-invocation action stale and replan it forever.
        _ = (workspace_id, project_id)
        return {
            entity_ref: self._versions[entity_ref]
            for entity_ref in entity_refs
            if entity_ref in self._versions
        }

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

    def get_workspace_sprints(self, workspace_id: str) -> list[dict[str, Any]]:
        data = self._request_json(
            "GET",
            f"/workspaces/{quote(workspace_id, safe='')}/sprints/internal",
        )
        return _extract_items(data)

    def add_sprint_work_items(
        self,
        workspace_id: str,
        sprint_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/workspaces/{quote(workspace_id, safe='')}/sprints/internal/"
            f"{quote(sprint_id, safe='')}/work-items",
            payload,
        )

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
            if exc.code == 400:
                raise PrismApiBadRequestError(
                    f"Prism API {method} {path} returned 400: {detail}{request_payload}"
                ) from exc
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
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Prism API {method} {path} unreachable: {exc}") from exc
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(f"Prism API {method} {path} returned invalid JSON.") from exc
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
        if _has_hydrated_pull_request_diff(explicit_value):
            _log_diff_hydration(event, source="payload", result=explicit_value, fetched=False)
            return explicit_value
        # PR-review events embed the changed files under pullRequest/pull_request
        # rather than a separate pull_request_diff entity, so the diff is already
        # in the payload — just under a different key. Prefer it over an API fetch
        # (which can't fire anyway when the event carries no project_id).
        embedded = _embedded_pull_request_diff(event.payload)
        pull_number = _pull_request_number(explicit_value) or _pull_request_number(event.payload)
        if _has_hydrated_pull_request_diff(embedded):
            _log_diff_hydration(
                event, source="embedded", result=embedded, fetched=False, pull_number=pull_number
            )
            return embedded
        if not (self.is_configured and event.project_id and pull_number is not None):
            resolved = explicit_value if isinstance(explicit_value, dict) else embedded
            _log_diff_hydration(
                event,
                source="payload",
                result=resolved,
                fetched=False,
                pull_number=pull_number,
                skip_reason=_diff_fetch_skip_reason(
                    self.is_configured, event.project_id, pull_number
                ),
            )
            return resolved
        try:
            fetched = self.get_pull_request(event.project_id, pull_number)
        except PrismApiNotFoundError:
            resolved = explicit_value if isinstance(explicit_value, dict) else embedded
            _log_diff_hydration(
                event,
                source="fetch",
                result=resolved,
                fetched=True,
                pull_number=pull_number,
                fetch_error="not_found",
            )
            return resolved
        _log_diff_hydration(
            event, source="fetch", result=fetched, fetched=True, pull_number=pull_number
        )
        return fetched

    @staticmethod
    def _pull_request_event_context(
        event: BaseRuntimeEvent,
        explicit_value: Any,
    ) -> dict[str, Any]:
        if isinstance(explicit_value, dict):
            return explicit_value
        payload = event.payload
        pull_request = payload.get("pull_request") or payload.get("pullRequest")
        context: dict[str, Any] = {}
        if isinstance(pull_request, dict):
            context["pullRequest"] = pull_request
        pull_number = _pull_request_number(payload)
        if pull_number is not None:
            context["pullNumber"] = pull_number
        for key in ("action", "repository", "sender", "installation"):
            value = payload.get(key)
            if value is not None:
                context[key] = value
        return context

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

    def _backlog_work_items_context(
        self,
        event: BaseRuntimeEvent,
        explicit_value: Any,
    ) -> list[dict[str, Any]]:
        """Open work items with the fields refinement signals need.

        Unlike the slim project_work_items view, refinement needs dates and a
        description excerpt to judge staleness and ambiguity; descriptions are
        truncated so a large backlog stays prompt-sized.
        """
        explicit_items = _extract_items(explicit_value)
        if explicit_items or not self.is_configured or not event.project_id:
            return explicit_items
        items: list[dict[str, Any]] = []
        for status in ("todo", "in_progress", "in_review"):
            if len(items) >= PROJECT_WORK_ITEMS_CONTEXT_LIMIT:
                break
            try:
                result = self.search_work_items(
                    event.project_id,
                    {
                        "status": status,
                        "limit": PROJECT_WORK_ITEMS_CONTEXT_LIMIT - len(items),
                    },
                )
            except RuntimeError:
                continue
            fetched = result.get("items")
            if isinstance(fetched, list):
                items.extend(_backlog_work_item(item) for item in fetched if isinstance(item, dict))
        return items

    def _sprints_context(
        self,
        event: BaseRuntimeEvent,
        explicit_value: Any,
    ) -> list[dict[str, Any]]:
        explicit_items = _extract_items(explicit_value)
        if explicit_items or not self.is_configured:
            return explicit_items
        try:
            sprints = self.get_workspace_sprints(event.workspace_id)
        except RuntimeError:
            return []
        return [_slim_sprint(sprint) for sprint in sprints[:SPRINTS_CONTEXT_LIMIT]]

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


def _pull_request_number(payload: Any) -> str | int | None:
    if not isinstance(payload, dict):
        return None
    for key in (
        "pullNumber",
        "pull_number",
        "pullRequestNumber",
        "pull_request_number",
        "githubPullNumber",
        "github_pull_number",
        "number",
    ):
        value = payload.get(key)
        if value is not None:
            return value
    for parent_key in (
        "pull_request",
        "pullRequest",
        "github_pull_request",
        "githubPullRequest",
        "queue_pointer",
        "queuePointer",
    ):
        nested = payload.get(parent_key)
        if isinstance(nested, dict):
            value = _pull_request_number(nested)
            if value is not None:
                return value
    return None


def _has_hydrated_pull_request_diff(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    files = value.get("files")
    if not isinstance(files, list):
        return False
    if value.get("truncated") is True:
        return True
    for file_diff in files:
        if isinstance(file_diff, dict) and str(file_diff.get("patch") or "").strip():
            return True
    return False


def _embedded_pull_request_diff(payload: Any) -> dict[str, Any] | None:
    """Lift the diff out of the pull request object the event embeds.

    PR-review events carry the changed files under ``pullRequest``/``pull_request``
    (``files[].patch``) instead of a separate ``pull_request_diff`` entity, so the
    hydrated diff is already in the payload — just under the key that feeds
    ``pull_request_event``. Return it in the pull_request_diff shape, backfilling
    the pull number and head SHA from the top level when the nested object omits
    them, so the review workflow can ground on it without an API fetch.
    """
    if not isinstance(payload, dict):
        return None
    pull_request = payload.get("pull_request") or payload.get("pullRequest")
    if not isinstance(pull_request, dict):
        return None
    diff = dict(pull_request)
    if diff.get("pullNumber") is None and diff.get("pull_number") is None:
        number = _pull_request_number(payload)
        if number is not None:
            diff["pullNumber"] = number
    if not diff.get("headSha") and not diff.get("head_sha"):
        head = payload.get("headSha") or payload.get("head_sha")
        if head:
            diff["headSha"] = head
    if not diff.get("repositoryFullName") and not diff.get("repository_full_name"):
        repo = payload.get("repositoryFullName") or payload.get("repository_full_name")
        if repo:
            diff["repositoryFullName"] = repo
    return diff


def _diff_files_count(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    files = value.get("files")
    return len(files) if isinstance(files, list) else None


def _diff_fetch_skip_reason(
    is_configured: bool,
    project_id: str | None,
    pull_number: Any,
) -> str:
    if not is_configured:
        return "client_not_configured"
    if not project_id:
        return "missing_project_id"
    if pull_number is None:
        return "missing_pull_number"
    return "unknown"


def _log_diff_hydration(
    event: BaseRuntimeEvent,
    *,
    source: str,
    result: Any,
    fetched: bool,
    pull_number: Any = None,
    skip_reason: str | None = None,
    fetch_error: str | None = None,
) -> None:
    """Record why pull_request_diff hydration did or didn't produce a usable diff.

    The hydration path used to return ``None`` silently, so a review that bailed
    with "diff unavailable" left no trace of the cause. This emits one structured
    line per attempt: INFO when the diff carries file patches, WARNING (with the
    precise reason) when it doesn't, so a fileless fetch or a skipped fetch is
    visible in function logs instead of surfacing only as a vague suggestion.
    """
    hydrated = _has_hydrated_pull_request_diff(result)
    record: dict[str, Any] = {
        "log": "diff.hydration",
        "event_type": event.event_type,
        "workspace_id": event.workspace_id,
        "project_id": event.project_id,
        "pull_number": pull_number,
        "source": source,
        "fetch_attempted": fetched,
        "files_count": _diff_files_count(result),
        "has_usable_diff": hydrated,
        "result_present": result is not None,
    }
    if skip_reason is not None:
        record["skip_reason"] = skip_reason
    if fetch_error is not None:
        record["fetch_error"] = fetch_error
    if not hydrated:
        record["guard"] = "pull_request_diff carries no file patches; review cannot be grounded"
    log_json(get_logger(), logging.INFO if hydrated else logging.WARNING, record)


PROJECT_WORK_ITEMS_CONTEXT_LIMIT = 100
SPRINTS_CONTEXT_LIMIT = 10
BACKLOG_DESCRIPTION_EXCERPT_LENGTH = 240
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


_BACKLOG_EXTRA_FIELDS = ("statusChangedAt", "createdAt")
_SPRINT_CONTEXT_FIELDS = ("sprintId", "name", "goal", "status", "startsAt", "endsAt")


def _slim_work_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        field: item[field] for field in _WORK_ITEM_CONTEXT_FIELDS if item.get(field) is not None
    }


def _backlog_work_item(item: dict[str, Any]) -> dict[str, Any]:
    slim = _slim_work_item(item)
    for field in _BACKLOG_EXTRA_FIELDS:
        if item.get(field) is not None:
            slim[field] = str(item[field])
    description = str(item.get("description") or "").strip()
    slim["description"] = description[:BACKLOG_DESCRIPTION_EXCERPT_LENGTH]
    return slim


def _slim_sprint(sprint: dict[str, Any]) -> dict[str, Any]:
    return {
        field: sprint[field] for field in _SPRINT_CONTEXT_FIELDS if sprint.get(field) is not None
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
