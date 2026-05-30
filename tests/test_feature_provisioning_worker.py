from __future__ import annotations

import pytest

from app.container import build_container
from application.feature_provisioning_worker import FeatureProvisioningWorker
from infrastructure.config.settings import Settings
from infrastructure.object_storage.memory_payload_store import MemoryPayloadStore


def _pointer() -> dict:
    return {
        "type": "feature.provisioning.requested",
        "version": "1.0",
        "requestId": "req-1",
        "payloadId": "payload-1",
        "payloadObjectName": "workspaces/w1/feature-provisioning/req-1.json",
        "payloadVersionId": "version-1",
        "workspaceId": "w1",
        "projectId": "p1",
        "requestedByUserId": "u1",
        "requestedAt": "2026-05-28T00:00:00Z",
    }


def _payload() -> dict:
    return {
        "requestId": "req-1",
        "workspace": {"workspaceId": "w1", "name": "Workspace"},
        "project": {"projectId": "p1", "name": "Project"},
        "featureSpecification": "Add roadmap import\n\nAcceptance: users can upload CSV files.",
        "workspaceMembers": [{"username": "alex"}],
    }


def test_feature_provisioning_worker_hydrates_payload_and_completes_plan() -> None:
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    payload_store = MemoryPayloadStore({_pointer()["payloadObjectName"]: _payload()})
    worker = FeatureProvisioningWorker(
        payload_store=payload_store,
        state_store=container.state_store,
        recursion_runner=container.recursion_runner,
    )

    result = worker.handle_pointer(_pointer())

    assert result.request_id == "req-1"
    assert result.event_id is not None
    assert result.duplicate is False
    assert container.state_store.check_idempotency_key("feature_provisioning:req-1") is True
    assert any(trace.event_name == "plan.completed" for trace in container.state_store.traces)
    plan = next(iter(container.state_store.plans.values()))
    assert [action.idempotency_key for action in plan.actions] == [
        "req-1:create_sprint:1",
        "req-1:create_workitem:2",
    ]


def test_feature_provisioning_worker_skips_duplicate_request() -> None:
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    container.state_store.record_idempotency_key("feature_provisioning:req-1")
    worker = FeatureProvisioningWorker(
        payload_store=MemoryPayloadStore({_pointer()["payloadObjectName"]: _payload()}),
        state_store=container.state_store,
        recursion_runner=container.recursion_runner,
    )

    result = worker.handle_pointer(_pointer())

    assert result.duplicate is True
    assert result.event_id is None


def test_feature_provisioning_worker_rejects_payload_identity_mismatch() -> None:
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    mismatched = {**_payload(), "project": {"projectId": "other-project"}}
    worker = FeatureProvisioningWorker(
        payload_store=MemoryPayloadStore({_pointer()["payloadObjectName"]: mismatched}),
        state_store=container.state_store,
        recursion_runner=container.recursion_runner,
    )

    with pytest.raises(ValueError, match="does not match pointer"):
        worker.handle_pointer(_pointer())
