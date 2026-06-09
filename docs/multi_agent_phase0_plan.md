# Phase 0 — Introduce the orchestration seam (no behavior change)

Goal: add the `OrchestrationPlan` / `TaskGraph` domain models and an `Orchestrator` that, for
now, emits a **single-node graph** delegating the whole workflow to one "do-everything"
subagent. The subagent is the *current* planner pipeline. End-to-end behavior is identical;
all existing tests stay green. This creates the seams that P1–P3 fill in.

Companion to `docs/multi_agent_architecture.md`.

## Principle

Phase 0 changes **structure, not behavior**. Every new branch defaults to the existing path.
No YAML changes are required to ship P0 (the orchestration block is read with a default that
produces one node covering all of a workflow's `required_skills`).

---

## Step 1 — Domain models: `src/domain/subtasks.py` (new)

```python
from __future__ import annotations
from enum import StrEnum
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field

class SubTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class SubTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    skill_ids: list[str]                 # P0 single node = all workflow skills
    objective: str
    depends_on: list[str] = Field(default_factory=list)
    context_scope: list[str] = Field(default_factory=list)   # [] = full context in P0
    model_tier: str = "pro"
    status: SubTaskStatus = SubTaskStatus.PENDING

class TaskGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    project_id: str | None = None
    nodes: list[SubTask] = Field(default_factory=list)
    synthesizer_node: SubTask | None = None

    def ready_nodes(self, completed: set[str]) -> list[SubTask]:
        return [
            n for n in self.nodes
            if n.status == SubTaskStatus.PENDING and set(n.depends_on) <= completed
        ]

class SubAgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    skill_ids: list[str]
    summary: str = ""
    structured_output: dict = Field(default_factory=dict)
    status: SubTaskStatus = SubTaskStatus.COMPLETED
```

No `OrchestrationPlan` wrapper yet — `TaskGraph` is enough for P0. Add the wrapper in P1
when sub-run grouping matters.

---

## Step 2 — Orchestrator: `src/application/orchestrator.py` (new)

P0 implementation builds a deterministic single-node graph from the workflow. No LLM call
yet (hybrid/dynamic decomposition arrives in P1).

```python
class Orchestrator:
    def __init__(self, workflow_registry, settings) -> None: ...

    def build_task_graph(self, context, workflow) -> TaskGraph:
        # P0: one node covering all workflow.required_skills, full context, "pro" tier.
        node = SubTask(
            node_id="root",
            skill_ids=list(workflow.required_skills),
            objective=workflow.goal,
            depends_on=[],
            context_scope=[],          # empty = full context (current behavior)
            model_tier="pro",
        )
        return TaskGraph(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            nodes=[node],
        )
```

---

## Step 3 — Subagent runner: `src/application/subagent_runner.py` (new, thin wrapper)

Wraps the **existing** `Planner` so a subagent's scope (skill subset) is honored. In P0 the
subset equals all workflow skills, so output equals today's plan.

```python
class SubAgentRunner:
    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    def plan_for_node(self, node: SubTask, context, snapshot_ref, workflow, agent_run_id):
        # P0: delegate straight to existing planner with the full workflow.
        # P1: pass node.skill_ids to restrict skills/tools and node.context_scope to slice.
        return self.planner.create_plan(context, snapshot_ref, workflow, agent_run_id)
```

This isolates the future change surface: when P1 narrows skills per node, only
`Planner.create_plan` gains an optional `skill_ids` / `context_scope` parameter and this
wrapper passes it through. `DomainEventHandler` does not change again.

---

## Step 4 — Wire orchestrator into `DomainEventHandler`

`src/application/domain_event_handler.py`, in `handle`, after `route` succeeds and the
snapshot is hydrated. Replace the direct `planner.create_plan(...)` call (currently around
the `try:` near line 90) with:

```python
task_graph = self.orchestrator.build_task_graph(snapshot.context, route.workflow)
self.state_store.save_task_graph(task_graph)            # new store method (Step 6)
root = task_graph.nodes[0]                               # P0: exactly one node
plan = self.subagent_runner.plan_for_node(
    root, snapshot.context, snapshot.ref, route.workflow, agent_run_id,
)
```

The rest of `handle` (replan detection, `save_plan`, `record_plan_created`, enqueue first
action) is unchanged. Because the graph has one node and the plan is identical, the trace
output and queued events match today exactly.

Constructor gains two params: `orchestrator: Orchestrator`, `subagent_runner: SubAgentRunner`.

---

## Step 5 — Events: `src/domain/events.py`

Add two events now (handlers wired in P1/P2), so serialization/discriminator support lands
early and the `RuntimeEvent` union is stable:

```python
class SubAgentTaskEvent(BaseRuntimeEvent):
    kind: Literal["subagent_task"] = "subagent_task"
    event_type: str = "agent.subagent.requested"
    plan_id: str
    node_id: str

class SubAgentCompletedEvent(BaseRuntimeEvent):
    kind: Literal["subagent_completed"] = "subagent_completed"
    event_type: str = "agent.subagent.completed"
    plan_id: str
    node_id: str
    status: str
```

Extend the `RuntimeEvent` union to include both. `RecursionRunner` needs no new branch in
P0 (no one emits these yet); branches arrive in P2.

---

## Step 6 — State store: `src/infrastructure/state/base.py` + impls

Add to the abstract base:

```python
@abstractmethod
def save_task_graph(self, graph: TaskGraph) -> None: ...
@abstractmethod
def get_task_graph(self, workspace_id: str, plan_id: str) -> TaskGraph | None: ...
```

- `MemoryStateStore` — dict keyed by `(workspace_id, plan_id)`, mirroring `save_plan`.
- `PrismApiStateStore` — P0 may persist as a JSON step/metadata attachment to the existing
  agent run (or no-op + in-memory cache) until the Prism API exposes a graph resource. Flag
  this as a follow-up; do not block P0 on an API change.

The atomic join **counter** (`increment_completed_nodes` etc.) is **not** added in P0 — it
belongs to P2 with the join coordinator.

---

## Step 7 — Container wiring: `src/app/container.py`

```python
orchestrator = Orchestrator(workflow_registry, settings)
subagent_runner = SubAgentRunner(planner)
domain_event_handler = DomainEventHandler(
    router, context_provider, planner, state_store, queue, agent_run_sync,
    orchestrator=orchestrator, subagent_runner=subagent_runner,
)
```

Add `orchestrator` and `subagent_runner` fields to `AppContainer`.

---

## Step 8 — Tests

New:
- `tests/test_orchestrator.py` — `build_task_graph` returns exactly one node whose
  `skill_ids == workflow.required_skills`, `context_scope == []`, `model_tier == "pro"`.
- `tests/test_subtasks.py` — `TaskGraph.ready_nodes` dependency logic (root ready when
  `completed` empty; dependents gated until deps complete).
- `tests/test_state_store_task_graph.py` — save/get round-trip on `MemoryStateStore`.

Unchanged (the P0 regression gate): `tests/test_planner_executor_validator.py`,
`tests/test_routing_and_memory.py`, `tests/test_agent_run.py`, `tests/test_local_recursion.py`
must pass untouched. If any changes, behavior leaked — fix the seam, not the test.

---

## Step 9 — Verify

```bash
uv run pytest -q
uv run ruff check .
```

Then run an existing seed event end-to-end and diff the trace against `develop` to confirm
identical `plan.created` / `action.enqueued` output:

```bash
uv run python -m interfaces.local_entry tests/fixtures/seed_events/story_created.json
```

---

## Done-when

- All pre-existing tests pass unchanged.
- A `story_created` seed produces a byte-identical trace (modulo new `task_graph.created`
  trace line, if you choose to add one) to `develop`.
- `Orchestrator`, `SubAgentRunner`, `TaskGraph`, `SubTask`, `SubAgentResult`,
  `SubAgentTaskEvent`, `SubAgentCompletedEvent`, and the two state-store methods exist and
  are wired, with no caller depending on more than one graph node yet.

## Explicitly deferred (do NOT build in P0)

- LLM-based decomposition / hybrid graph from YAML  → P1
- Per-node skill/context narrowing in `Planner`     → P1
- Synthesizer / reduce step                         → P1
- Parallel fan-out + `SubAgentTaskEvent` handler    → P2
- Atomic join barrier / counter in state store      → P2
- Per-tier model resolution in `GeminiModelProvider`→ P3
- Lazy scoped hydration in `ContextProvider`        → P3
