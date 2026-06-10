from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.container import build_container
from application.feature_provisioning_worker import FeatureProvisioningWorker
from domain.events import AgentActionEvent, EventEnvelope
from domain.feature_provisioning import (
    FeatureProvisioningPayload,
    FeatureProvisioningPointerEvent,
)
from infrastructure.config.settings import Settings
from infrastructure.object_storage.memory_payload_store import MemoryPayloadStore
from infrastructure.object_storage.oci_payload_store import ObjectStoragePayloadStore
from interfaces.local_entry import print_trace

FIXTURES_DIR = Path(__file__).parent / "fixtures"
POINTER_FIXTURE = FIXTURES_DIR / "feature_provisioning_pointer.local.json"
LIVE_INTEGRATION_FLAG = "RUN_FEATURE_PROVISIONING_LIVE_INTEGRATION"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pointer() -> dict:
    return _load_json(POINTER_FIXTURE)


def _local_payload(pointer: dict) -> dict:
    return {
        "requestId": pointer["requestId"],
        "workspace": {
            "workspaceId": pointer["workspaceId"],
            "name": "Local Workspace",
        },
        "project": {
            "projectId": pointer["projectId"],
            "name": "Local Project",
        },
        "featureSpecification": "Provision a test feature for local recursion.",
        "workspaceMembers": [{"username": "alex"}],
    }


def _load_dotenv() -> None:
    env_path = Path(__file__).parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _live_integration_settings() -> Settings:
    _load_dotenv()
    missing = [
        name
        for name in ("GEMINI_API_KEY", "PRISM_API_BASE_URL", "PRISM_API_TOKEN")
        if not os.getenv(name)
    ]
    if not os.getenv("OCI_OBJECT_STORAGE_NAMESPACE"):
        missing.append("OCI_OBJECT_STORAGE_NAMESPACE")
    if not os.getenv("OCI_OBJECT_STORAGE_BUCKET_NAME"):
        missing.append("OCI_OBJECT_STORAGE_BUCKET_NAME")
    if missing:
        pytest.skip(f"missing live integration env vars: {', '.join(missing)}")
    return Settings.from_env().model_copy(
        update={
            "app_env": "prod",
            "queue_backend": "memory",
            "state_backend": "prism_api",
        }
    )


def test_feature_provisioning_worker_dispatches_first_action_to_queue() -> None:
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    pointer = _pointer()
    payload_store = MemoryPayloadStore({pointer["payloadObjectName"]: _local_payload(pointer)})
    worker = FeatureProvisioningWorker(
        payload_store=payload_store,
        state_store=container.state_store,
        domain_event_handler=container.domain_event_handler,
    )

    result = worker.handle_pointer(pointer)

    assert result.request_id == pointer["requestId"]
    assert result.event_id is not None
    assert result.duplicate is False
    assert (
        container.state_store.check_idempotency_key(f"feature_provisioning:{pointer['requestId']}")
        is True
    )

    # Planning happened in this step; execution did not. The first action is enqueued
    # for a later invocation rather than drained in-session.
    plan = next(iter(container.state_store.plans.values()))
    assert plan.plan_id == pointer["requestId"]
    assert [action.idempotency_key for action in plan.actions] == [
        f"{pointer['requestId']}:create_sprint:1",
        f"{pointer['requestId']}:create_workitem:2",
    ]
    trace_names = {trace.event_name for trace in container.state_store.traces}
    assert "plan.created" in trace_names
    assert "action.enqueued" in trace_names
    assert "plan.completed" not in trace_names
    assert "action.completed" not in trace_names

    queued = container.queue.dequeue()
    assert queued is not None and container.queue.is_empty()
    queued_event = queued.envelope.event
    assert isinstance(queued_event, AgentActionEvent)
    assert queued_event.plan_id == plan.plan_id
    assert queued_event.action_id == plan.actions[0].action_id


def test_feature_provisioning_worker_skips_duplicate_request() -> None:
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    pointer = _pointer()
    container.state_store.record_idempotency_key(f"feature_provisioning:{pointer['requestId']}")
    worker = FeatureProvisioningWorker(
        payload_store=MemoryPayloadStore({pointer["payloadObjectName"]: _local_payload(pointer)}),
        state_store=container.state_store,
        domain_event_handler=container.domain_event_handler,
    )

    result = worker.handle_pointer(pointer)

    assert result.duplicate is True
    assert result.event_id is None
    assert container.queue.is_empty()


def test_feature_provisioning_worker_rejects_payload_identity_mismatch() -> None:
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    pointer = _pointer()
    mismatched = {**_local_payload(pointer), "project": {"projectId": "other-project"}}
    worker = FeatureProvisioningWorker(
        payload_store=MemoryPayloadStore({pointer["payloadObjectName"]: mismatched}),
        state_store=container.state_store,
        domain_event_handler=container.domain_event_handler,
    )

    with pytest.raises(ValueError, match="does not match pointer"):
        worker.handle_pointer(pointer)


def test_feature_provisioning_worker_propagates_dispatch_failure_without_recording_key() -> None:
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    pointer = _pointer()
    worker = FeatureProvisioningWorker(
        payload_store=MemoryPayloadStore({pointer["payloadObjectName"]: _local_payload(pointer)}),
        state_store=container.state_store,
        domain_event_handler=_RaisingDomainEventHandler(),
    )

    with pytest.raises(RuntimeError, match="planner exploded"):
        worker.handle_pointer(pointer)

    # The pointer stays retryable: a transient dispatch failure must not record the
    # idempotency key.
    assert (
        container.state_store.check_idempotency_key(f"feature_provisioning:{pointer['requestId']}")
        is False
    )


def test_feature_provisioning_pipeline_completes_via_recursion_runner() -> None:
    # End-to-end coverage of the full workflow, simulated in-process the way local
    # dev drains the memory queue. Production fans these steps out across invocations.
    container = build_container(Settings(queue_backend="memory", state_backend="memory"))
    pointer_dict = _pointer()
    pointer = FeatureProvisioningPointerEvent.model_validate(pointer_dict)
    payload = FeatureProvisioningPayload.model_validate(_local_payload(pointer_dict))
    event = payload.to_domain_event(pointer)

    traces = container.recursion_runner.run(event)

    assert any(trace.event_name == "plan.completed" for trace in traces)
    plan = next(iter(container.state_store.plans.values()))
    assert [action.idempotency_key for action in plan.actions] == [
        f"{pointer_dict['requestId']}:create_sprint:1",
        f"{pointer_dict['requestId']}:create_workitem:2",
    ]
    assert [action.input["requestedByUserId"] for action in plan.actions] == [
        pointer_dict["requestedByUserId"],
        pointer_dict["requestedByUserId"],
    ]


@pytest.mark.integration
def test_feature_provisioning_worker_live_api_calls_with_prism_api_state() -> None:
    _load_dotenv()
    if os.getenv(LIVE_INTEGRATION_FLAG) != "1":
        pytest.skip(f"set {LIVE_INTEGRATION_FLAG}=1 to run live Gemini and Prism API calls")

    settings = _live_integration_settings()
    container = build_container(settings)
    pointer = _pointer()
    worker = FeatureProvisioningWorker(
        payload_store=ObjectStoragePayloadStore(
            settings.object_storage_namespace,
            settings.object_storage_bucket_name,
        ),
        state_store=container.state_store,
        domain_event_handler=container.domain_event_handler,
    )

    result = worker.handle_pointer(pointer)
    print_trace(container.state_store.traces)

    assert result.request_id == pointer["requestId"]
    assert result.event_id is not None
    assert result.duplicate is False
    assert container.settings.state_backend == "prism_api"
    # Single-step dispatch: planning ran against live Gemini/Prism and the first
    # action is enqueued for a later invocation.
    trace_names = {trace.event_name for trace in container.state_store.traces}
    assert "plan.created" in trace_names
    assert "action.enqueued" in trace_names
    assert not container.queue.is_empty()
    assert not any(
        name in {"event.failed", "action.failed", "plan.failed", "recursion.max_depth"}
        for name in trace_names
    )


class _RaisingDomainEventHandler:
    def handle(self, envelope: EventEnvelope) -> None:
        _ = envelope
        raise RuntimeError("planner exploded")
