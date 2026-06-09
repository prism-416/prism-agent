# Multi-Agent Architecture

This document proposes evolving `prism-agent` from a single-agent, monolithic-planner
pipeline into an **orchestrator → subagents → tools** hierarchy, and explains why.

Status: **design / proposal**. No runtime code has been changed yet.

Decisions locked with the team:

- **Concurrency:** distributed via the OCI Queue (fan-out to parallel invocations, joined
  by a state-store barrier).
- **Decomposition:** hybrid — each workflow declares a default subagent graph; the
  orchestrator LLM may prune/add nodes from hydrated context.
- **Model tiering:** role-based (strong model for orchestrator + reasoning subagents,
  fast/cheap model for extraction subagents).

---

## 1. Current architecture and its bottleneck

Today's flow is a monolithic planner feeding a strictly-sequential executor:

```text
Event → EventRouter → ContextProvider.hydrate (ALL required_context, up front)
      → Planner → ONE Gemini call → entire AgentPlan
      → actions force-chained linearly (depends_on = [previous])
      → ActionEventHandler executes ONE action at a time
```

Three concrete chokepoints:

1. **The single planning call is the cognitive bottleneck.**
   `RuntimePlanningAgent._generate_plan_with_pydantic_ai`
   (`src/infrastructure/llm/pydantic_ai_agent_factory.py`) builds one prompt holding the
   workflow + **all** skills + **all** tools + the **entire** serialized context
   (`context.model_dump_json()`), and asks one model to emit the whole `AgentPlan`.
   For `story.decompose` that is 3 skills, 3+ tools, and ~11 hydrated entities in a single
   prompt. More capability → worse precision.

2. **Execution is artificially serialized.**
   `Planner._normalize_plan_for_runtime` (`src/application/planner.py`) sets
   `depends_on = [previous_action_id]` for **every** action — even independent ones — and
   `ActionEventHandler._enqueue_next_action` (`src/application/action_event_handler.py`)
   enqueues the next action only after the current commits. N independent actions cost N
   sequential round-trips. No parallelism, despite a queue that could fan out.

3. **Context is over-hydrated and single-tier.**
   `ContextProvider.hydrate` (`src/application/context_provider.py`) fetches every
   `required_context` entity for the whole workflow before any reasoning, and the full blob
   enters the planning prompt. One model tier serves both trivial extraction and hard
   reasoning.

The event queue + `RecursionRunner` + clean layering is the right substrate for
multi-agent work — the fix is a new layer above it, not a rewrite.

---

## 2. Target architecture

```text
                    ┌─────────────────────────────────────────┐
   Event ──Router──▶│  ORCHESTRATOR (lead agent, strong model) │
                    │  decomposes goal → TaskGraph (DAG)        │
                    └───────────────┬───────────────────────────┘
                                    │ emits N SubAgentTaskEvents
                   ┌────────────────┼────────────────┐
                   ▼                ▼                ▼          (parallel fan-out
        ┌──────────────┐ ┌──────────────┐ ┌──────────────┐      over the OCI Queue)
        │  SUBAGENT A  │ │  SUBAGENT B  │ │  SUBAGENT C  │
        │ skill=decomp │ │ skill=risk   │ │ skill=backlog│
        │ own tools    │ │ own tools    │ │ own tools    │
        │ context SLICE│ │ context SLICE│ │ context SLICE│
        │ own model    │ │ own model    │ │ own model    │
        │ plan→exec→val│ │ plan→exec→val│ │ plan→exec→val│  ← existing pipeline,
        └──────┬───────┘ └──────┬───────┘ └──────┬───────┘     scoped per subagent
               └─────────── fan-in / join ───────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  SYNTHESIZER (reduce → workflow output)   │
                    └─────────────────────────────────────────┘
```

A **subagent is your existing `Skill` + its tool whitelist, promoted to a first-class
runtime actor** with its own context slice and model tier. Skills already carry
`allowed_tools`, so they are most of a subagent definition already.

Roles:

- **Orchestrator** — reasons only about delegation: which subagents to run, each one's
  objective, and the dependency edges. Emits a `TaskGraph`, not tool calls. Strong model,
  small output.
- **Subagent (worker)** — runs the existing Planner → Executor → Validator loop, restricted
  to one skill, that skill's tools, and only its declared context slice. Returns a
  structured `SubAgentResult` summary.
- **Synthesizer** — folds subagent results into the workflow's final artifact.

### Why this resolves the chokepoints

| Bottleneck | Fix |
|---|---|
| Monolithic planner | Each subagent plans over one skill + its tools + its slice → small, focused prompt → higher precision. Orchestrator's prompt is tiny. |
| Serial execution | Orchestrator emits a DAG; independent nodes fan out as parallel queue events. `depends_on` becomes real dependency, not a forced chain. |
| Over-hydration / single tier | Lazy, scoped hydration per subagent. Per-agent model tiers: strong for reasoning, fast for extraction. |

Precision comes from context isolation; power/throughput from parallel fan-out.

---

## 3. New domain models (`src/domain/subtasks.py`)

```text
SubTask        { node_id, skill_id, objective, depends_on: [node_id],
                 context_scope: [entity_keys], model_tier, status }
TaskGraph      { plan_id, nodes: [SubTask], synthesizer_node: SubTask | None }
SubAgentResult { node_id, skill_id, summary, structured_output, status }
```

`OrchestrationPlan` wraps the `TaskGraph`. Each subagent still produces a normal
`AgentPlan`, so Planner / Executor / Validator are reused unchanged inside subagent scope.

---

## 4. New events (`src/domain/events.py`)

Both mirror the existing `AgentActionEvent`, so `RecursionRunner` handles them with one new
branch each.

```text
SubAgentTaskEvent       # orchestrator → subagent; node_id, skill_id, context_slice_ref
SubAgentCompletedEvent  # subagent → join coordinator; node_id, result_ref, status
```

---

## 5. YAML changes (backward compatible — definitions use `extra="allow"`)

Workflow declares the default DAG (hybrid mode lets the orchestrator prune/add):

```yaml
# prompts/workflows/story.decompose.v1.yaml  (added)
orchestration:
  mode: hybrid                      # hybrid | static | dynamic
  default_graph:
    - node: decompose
      skill: task_decomposition
      context_scope: [work_item, child_work_items, project_members, member_workloads]
    - node: risk
      skill: risk_detection
      context_scope: [work_item, recent_sprints]
      depends_on: []                # independent → parallel with decompose
    - node: backlog
      skill: backlog_analysis
      context_scope: [sibling_work_items, work_item]
  synthesizer:
    skill: project_summary
    depends_on: [decompose, risk, backlog]
```

Skill declares its model tier:

```yaml
# prompts/skills/task_decomposition.v1.yaml  (added)
model_tier: pro      # pro | flash
# e.g. find_duplicate_workitems skill → model_tier: flash
```

---

## 6. Execution flow (distributed queue)

1. `DomainEventHandler` routes, then the **Orchestrator** produces a `TaskGraph` (one strong
   model call; hydrates only the union of declared `context_scope`s, lazily).
2. Orchestrator persists the graph and enqueues all dependency-free nodes as
   `SubAgentTaskEvent`s. The OCI Queue fans them to parallel invocations.
3. Each `SubAgentTaskHandler` runs the scoped Planner → Executor → Validator on its slice
   with its `model_tier`, writes a `SubAgentResult`, and emits `SubAgentCompletedEvent`.
4. The **join coordinator** (atomic counter in the state store, idempotent via the existing
   `idempotency_key`) records each completion, enqueues newly-unblocked nodes, and — once
   the synthesizer's dependencies are met — enqueues the synthesizer.
5. The **Synthesizer** reduces results into the workflow output and closes the `agent_run`.

---

## 7. Reused vs. new vs. changed

**Reused unchanged:** `Planner`, `Executor`, `Validator`, `ToolRegistry`, queue, state
store, `AgentRunSync`, idempotency/recursion guards.

**New:** `Orchestrator`, `SubAgentTaskHandler`, join coordinator, `synthesizer`, two event
types, `domain/subtasks.py`, `SubAgentRegistry` (skills + tier + scope),
`create_orchestrator` / `create_subagent` factory methods.

**Changed minimally:** `RecursionRunner` (+2 branches), `ContextProvider` (lazy/scoped
hydration), `GeminiModelProvider` (tier lookup), skill/workflow YAML (additive fields).

---

## 8. Phased rollout

- **P0 — done.** Domain models + orchestrator emitting a single-node graph
  (behavior-identical; seams exist; tests green). See `docs/multi_agent_phase0_plan.md`.
- **P1 — done.** Orchestration schema (`orchestration:` on workflows, `model_tier` on
  skills), `Orchestrator.build_orchestrated_graph`, per-node scoped planning
  (`Planner` skill/context-scope/plan_id args), `SubAgentCoordinator` driving sequential
  dispatch → join → synthesizer over the queue, flat agent-run sync (sub-plans report under
  the parent run id, node-tagged steps). First workflow flipped on: `sprint.report`.
- **P2 — partial.** Parallel fan-out shipped: the initial dispatch claims and enqueues
  **all** ready nodes, and the join is a single atomic, idempotent state-store op
  (`advance_task_graph`) backed by pure `TaskGraph.plan_advance` — duplicate completion
  events cannot double-dispatch the synthesizer or re-finalize the run. Still open: a
  **transactional/CAS task-graph store** for cross-invocation atomicity (see below), and
  orchestrated replan/approval-resume across nodes.
- **P3 — done.** `model_tier` now binds to a concrete model: `GeminiModelProvider` resolves
  `pro`→`default_gemini_model`, `flash`→`gemini_flash_model`, threaded
  `SubTask.model_tier → Planner → agent factory` (the inline whole-workflow path passes no
  tier, so it stays on the prompt's model). `ContextProvider` does **lazy scoped hydration**:
  an orchestrated workflow fetches only the union of its node `context_scope`s.
- **Durability — done (atomicity partial).** `PrismApiStateStore` now persists and restores
  the task graph and subagent results across invocations (new `task_graph` /
  `sub_agent_result` state-memory kinds), and `advance_task_graph` / `claim_ready_nodes`
  read the latest durable graph before mutating, bumping `TaskGraph.version`. Advances are
  idempotent across invocations (a redelivered completion does not re-bump the version).

### Known limitations (deferred)
- **Cross-invocation write race.** Durability + read-latest + `version` give optimistic
  concurrency, but the Prism API has no conditional write, so two *simultaneous* writers can
  still lose an update — detection, not prevention. Closing it needs an API compare-and-set
  (If-Match on `version`) or a transactional task-graph resource. The `version` field is the
  seam for that. Also note graph persistence rides on `persist_agent_memories`.
- A sub-plan hitting **stale context** is settled as failed (orchestrated replan not yet
  wired); **waiting-for-approval** pauses the run without cross-node resume.
- The flat agent-run sync tags steps by node but does not group sub-runs hierarchically.

---

## 9. Risks to watch

- **Join correctness under at-least-once delivery.** The barrier must be atomic and
  idempotent. The existing `_seen_event_ids` + `idempotency_key` cover most of it, but
  `PrismApiStateStore` needs a real atomic counter, not just the in-memory set.
- **`max_recursion_depth`** (`RecursionRunner`) now counts orchestration + N subagents +
  synthesizer. Rebalance it, or give each subagent a separate depth budget.
- **Agent-run schema.** The Prism API models a run as one `agent_runs` + flat `steps` /
  `actions`. A hierarchical run (orchestrator + sub-runs) may want sub-run grouping —
  confirm whether `AgentStepResponseDto` can represent the tree, or keep steps flat with a
  `node_id` tag.
