from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.events import DomainEvent, utc_now


class FeatureProvisioningPointerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["feature.provisioning.requested"]
    version: Literal["1.0"]
    request_id: str = Field(alias="requestId")
    agent_run_id: str | None = Field(default=None, alias="agentRunId")
    payload_id: str | None = Field(default=None, alias="payloadId")
    payload_object_name: str = Field(alias="payloadObjectName")
    payload_version_id: str | None = Field(default=None, alias="payloadVersionId")
    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    requested_by_user_id: str | None = Field(default=None, alias="requestedByUserId")
    requested_at: datetime | None = Field(default=None, alias="requestedAt")

    @property
    def worker_idempotency_key(self) -> str:
        return f"feature_provisioning:{self.request_id}"


class FeatureProvisioningPayload(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    request_id: str | None = Field(default=None, alias="requestId")
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    project_id: str | None = Field(default=None, alias="projectId")
    feature_specification: Any = Field(default=None, alias="featureSpecification")
    workspace: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    workspace_members: list[dict[str, Any]] = Field(default_factory=list, alias="workspaceMembers")

    def validate_matches(self, pointer: FeatureProvisioningPointerEvent) -> None:
        if (
            not isinstance(self.feature_specification, str)
            or not self.feature_specification.strip()
        ):
            raise ValueError("Feature provisioning payload missing featureSpecification.")
        expected = {
            "requestId": pointer.request_id,
            "workspaceId": pointer.workspace_id,
            "projectId": pointer.project_id,
        }
        actual = {
            "requestId": self._first_present("requestId", "request_id"),
            "workspaceId": self._first_present(
                "workspaceId",
                "workspace_id",
                ("workspace", "workspaceId"),
                ("workspace", "id"),
            ),
            "projectId": self._first_present(
                "projectId",
                "project_id",
                ("project", "projectId"),
                ("project", "id"),
            ),
        }
        missing = sorted(field for field, value in actual.items() if value is None)
        if missing:
            raise ValueError(f"Feature provisioning payload missing identity fields: {missing}")
        mismatches = {
            field: {"pointer": expected[field], "payload": actual[field]}
            for field in expected
            if str(actual[field]) != str(expected[field])
        }
        if mismatches:
            raise ValueError(f"Feature provisioning payload does not match pointer: {mismatches}")

    def to_domain_event(self, pointer: FeatureProvisioningPointerEvent) -> DomainEvent:
        payload = self.model_dump(by_alias=True, exclude_none=True)
        payload["feature_specification"] = self.feature_specification
        payload["workspace_members"] = self.workspace_members
        payload["queue_pointer"] = pointer.model_dump(by_alias=True, exclude_none=True)
        if pointer.agent_run_id:
            payload["agentRunId"] = pointer.agent_run_id
        return DomainEvent(
            event_type=pointer.type,
            workspace_id=pointer.workspace_id,
            project_id=pointer.project_id,
            payload=payload,
            occurred_at=pointer.requested_at or utc_now(),
            correlation_id=pointer.request_id,
            idempotency_key=pointer.request_id,
        )

    def _first_present(self, *paths: str | tuple[str, str]) -> Any:
        data = self.model_dump(by_alias=True, exclude_none=True)
        extra = self.model_extra or {}
        for path in paths:
            if isinstance(path, str):
                if path in data:
                    return data[path]
                if path in extra:
                    return extra[path]
                continue
            parent, child = path
            container = data.get(parent) or extra.get(parent)
            if isinstance(container, dict) and child in container:
                return container[child]
        return None
