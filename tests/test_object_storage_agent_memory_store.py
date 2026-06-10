from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from domain.context import AgentContext, ContextSnapshot
from domain.events import DomainEvent, EventEnvelope
from domain.plans import AgentPlan
from infrastructure.object_storage.oci_agent_memory_store import ObjectStorageAgentMemoryStore
from infrastructure.state.memory_state_store import MemoryStateStore
from infrastructure.state.prism_api_state_store import PrismApiStateStore


class _FakeObjectStorageClient:
    """In-memory stand-in for oci.object_storage.ObjectStorageClient."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(
        self, namespace: str, bucket: str, object_name: str, body: bytes, **_: Any
    ) -> SimpleNamespace:
        _ = (namespace, bucket)
        self.objects[object_name] = body
        return SimpleNamespace()

    def list_objects(
        self, namespace: str, bucket: str, prefix: str = "", **_: Any
    ) -> SimpleNamespace:
        _ = (namespace, bucket)
        names = sorted(name for name in self.objects if name.startswith(prefix))
        return SimpleNamespace(
            data=SimpleNamespace(objects=[SimpleNamespace(name=name) for name in names])
        )

    def get_object(
        self, namespace: str, bucket: str, object_name: str, **_: Any
    ) -> SimpleNamespace:
        _ = (namespace, bucket)
        return SimpleNamespace(data=self.objects[object_name])


class _ConfiguredPrismClient:
    """Prism client whose only relevant property is that it is configured; the
    Object Storage transport handles all durable state, so no method is called."""

    is_configured = True


def test_object_storage_memory_store_round_trips_records_by_run() -> None:
    client = _FakeObjectStorageClient()
    store = ObjectStorageAgentMemoryStore("ns", "bucket", client=client)

    store.put_memory("w1", "run-1", {"memoryId": "m1", "content": '{"kind":"x"}'})
    store.put_memory("w1", "run-1", {"memoryId": "m2", "content": '{"kind":"y"}'})
    store.put_memory("w1", "run-2", {"memoryId": "m3", "content": '{"kind":"z"}'})

    memories = store.fetch_run_state("w1", "run-1")["memories"]
    assert sorted(memory["memoryId"] for memory in memories) == ["m1", "m2"]
    # Re-putting the same memoryId overwrites in place rather than duplicating.
    store.put_memory("w1", "run-1", {"memoryId": "m1", "content": '{"kind":"x2"}'})
    assert len(store.fetch_run_state("w1", "run-1")["memories"]) == 2


def test_object_storage_transport_persists_plan_and_snapshot_across_instances() -> None:
    oci_client = _FakeObjectStorageClient()

    def make_store() -> PrismApiStateStore:
        return PrismApiStateStore(
            _ConfiguredPrismClient(),
            MemoryStateStore(),
            persist_agent_memories=True,
            memory_store=ObjectStorageAgentMemoryStore("ns", "bucket", client=oci_client),
        )

    source = EventEnvelope.wrap(
        DomainEvent(
            event_type="feature.provisioning.requested",
            workspace_id="w1",
            project_id="p1",
        )
    )
    context = AgentContext(
        workspace_id="w1", project_id="p1", source_event=source, workflow_id="wf-1"
    )
    snapshot = ContextSnapshot(
        plan_id="run-1", workspace_id="w1", project_id="p1", workflow_id="wf-1", context=context
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id="evt-1",
        workspace_id="w1",
        project_id="p1",
        goal="provision",
        prompt_id="project_manager",
        prompt_version="1.0.0",
        context_snapshot_ref=snapshot.ref,
    )

    writer = make_store()
    writer.save_plan(plan)
    writer.save_context_snapshot(snapshot)

    # A fresh process (cold fallback) reads everything back from Object Storage.
    reader = make_store()
    restored_plan = reader.get_plan("w1", "run-1")
    restored_snapshot = reader.get_context_snapshot("w1", snapshot.ref)

    assert restored_plan is not None
    assert restored_plan.context_snapshot_ref == snapshot.ref
    assert restored_snapshot is not None
    assert restored_snapshot.context.workflow_id == "wf-1"
