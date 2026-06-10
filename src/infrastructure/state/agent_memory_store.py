from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from infrastructure.prism_api.client import PrismApiClient, PrismApiNotFoundError


@runtime_checkable
class AgentStateMemoryStore(Protocol):
    """Durable transport for agent-state memory records.

    A "memory" is the upsert payload built by ``PrismApiStateStore`` (it carries a
    ``memoryId``, ``runId``, and a JSON ``content`` blob). Implementations persist
    these keyed by run so a later function invocation can replay them.

    ``fetch_run_state`` returns a run-state dict with at least a ``memories`` list;
    a transport may also include ``actions``/``run`` for action-status hydration.
    """

    def put_memory(self, workspace_id: str, run_id: str, payload: dict[str, Any]) -> None: ...

    def fetch_run_state(self, workspace_id: str, run_id: str) -> dict[str, Any]: ...


class PrismAgentMemoryStore:
    """Memory transport backed by Prism's agent-memories write + agent-run state read.

    Writes require bearer/JWT scope, so this transport is only usable by clients
    authenticated as a user — not the internal-token function runtime. The run state
    it returns carries both ``memories`` and ``actions`` (action-status hydration).
    """

    def __init__(self, prism_client: PrismApiClient) -> None:
        self.prism_client = prism_client

    def put_memory(self, workspace_id: str, run_id: str, payload: dict[str, Any]) -> None:
        _ = run_id
        self.prism_client.upsert_agent_memory(workspace_id, payload)

    def fetch_run_state(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        try:
            return self.prism_client.get_agent_run_state(workspace_id, run_id)
        except PrismApiNotFoundError:
            return {}
