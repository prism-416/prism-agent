from __future__ import annotations

from application.agent_run_sync import AgentRunSync
from application.subagent_runner import SubAgentRunner, sub_plan_id
from domain.actions import PlannedAction
from domain.events import (
    AgentActionEvent,
    EventEnvelope,
    RuntimeEvent,
    SubAgentCompletedEvent,
    SubAgentTaskEvent,
)
from domain.plans import AgentPlan, PlanStatus
from domain.results import TraceEvent
from domain.subtasks import SubAgentResult, SubTask, SubTaskStatus, TaskGraph
from infrastructure.queue.base import Queue
from infrastructure.registries.workflow_registry import WorkflowRegistry
from infrastructure.state.base import StateStore

SUBAGENT_RESULTS_KEY = "subagent_results"


class SubAgentCoordinator:
    """Drives a TaskGraph: dispatches subagent nodes, joins their results, runs the
    synthesizer, and finalizes the shared agent run.

    Phase 1 is sequential — one node in flight at a time. The same join logic
    generalizes to parallel fan-out in Phase 2 once the completion counter is made
    atomic in the persistent state store.
    """

    def __init__(
        self,
        state_store: StateStore,
        queue: Queue,
        subagent_runner: SubAgentRunner,
        agent_run_sync: AgentRunSync,
        workflow_registry: WorkflowRegistry,
    ) -> None:
        self.state_store = state_store
        self.queue = queue
        self.subagent_runner = subagent_runner
        self.agent_run_sync = agent_run_sync
        self.workflow_registry = workflow_registry

    def handle(self, envelope: EventEnvelope) -> None:
        event = envelope.event
        if isinstance(event, SubAgentTaskEvent):
            self._handle_task(envelope, event)
        elif isinstance(event, SubAgentCompletedEvent):
            self._handle_completed(envelope, event)
        else:
            raise TypeError("SubAgentCoordinator only handles subagent events.")

    # -- task dispatch -----------------------------------------------------

    def _handle_task(self, envelope: EventEnvelope, event: SubAgentTaskEvent) -> None:
        run_id = event.plan_id
        graph = self.state_store.get_task_graph(event.workspace_id, run_id)
        if graph is None:
            self._trace(event, "orchestration.graph_missing", f"No task graph for run {run_id}.")
            return
        node = graph.get_node(event.node_id)
        if node is None:
            self._trace(event, "orchestration.node_missing", f"Unknown node {event.node_id}.")
            return
        if node.status in (SubTaskStatus.COMPLETED, SubTaskStatus.FAILED):
            self._trace(
                event,
                "subagent.skipped",
                f"Node {node.node_id} already {node.status.value}; skipping duplicate task.",
                data={"node_id": node.node_id, "status": node.status.value},
            )
            return
        existing_sub_plan = self.state_store.get_plan(
            event.workspace_id, sub_plan_id(run_id, node.node_id)
        )
        if existing_sub_plan is not None:
            self._resume_existing_subplan(envelope, graph, node, existing_sub_plan)
            return

        snapshot = self.state_store.get_context_snapshot(
            event.workspace_id, graph.context_snapshot_ref or ""
        )
        if snapshot is None:
            self._trace(event, "orchestration.context_missing", f"No context for run {run_id}.")
            return

        context = snapshot.context
        if self._is_synthesizer(graph, node):
            context = self._inject_results(context, event.workspace_id, run_id)

        workflow = self.workflow_registry.get(context.workflow_id)
        self._trace(
            event,
            "subagent.planning",
            f"Planning subagent node {node.node_id}.",
            data={"node_id": node.node_id, "skill_ids": node.skill_ids},
        )

        sub_plan = self.subagent_runner.plan_for_node(node, context, snapshot.ref, workflow, run_id)
        self.state_store.save_plan(sub_plan)
        self.agent_run_sync.record_plan_created(sub_plan, context)

        first_action = sub_plan.next_pending_action()
        if first_action is None:
            self._trace(
                event,
                "subagent.empty",
                f"Subagent node {node.node_id} produced no actions; settling completed.",
                data={"node_id": node.node_id, "sub_plan_id": sub_plan.plan_id},
            )
            self._enqueue_completed(envelope, graph, node.node_id, "completed")
            return

        action_event = self._enqueue_action(envelope, sub_plan, first_action)
        self._trace(
            event,
            "subagent.started",
            f"Started subagent node {node.node_id} (sub-plan {sub_plan.plan_id}).",
            data={
                "node_id": node.node_id,
                "sub_plan_id": sub_plan.plan_id,
                "first_action_id": first_action.action_id,
                "action_event_id": action_event.event_id,
            },
        )

    def _resume_existing_subplan(
        self,
        envelope: EventEnvelope,
        graph: TaskGraph,
        node: SubTask,
        sub_plan: AgentPlan,
    ) -> None:
        event = envelope.event
        next_action = sub_plan.next_pending_action()
        if next_action is not None:
            action_event = self._enqueue_action(envelope, sub_plan, next_action)
            self._trace(
                event,
                "subagent.resumed",
                f"Resumed subagent node {node.node_id} from existing sub-plan.",
                data={
                    "node_id": node.node_id,
                    "sub_plan_id": sub_plan.plan_id,
                    "next_action_id": next_action.action_id,
                    "action_event_id": action_event.event_id,
                },
            )
            return

        if sub_plan.status in {PlanStatus.COMPLETED, PlanStatus.PLANNED}:
            self._trace(
                event,
                "subagent.resumed",
                f"Resumed completed subagent node {node.node_id} from existing sub-plan.",
                data={
                    "node_id": node.node_id,
                    "sub_plan_id": sub_plan.plan_id,
                    "plan_status": sub_plan.status.value,
                },
            )
            self._enqueue_completed(envelope, graph, node.node_id, "completed")
            return

        if sub_plan.status == PlanStatus.WAITING_FOR_APPROVAL:
            self._trace(
                event,
                "subagent.resumed",
                f"Resumed waiting subagent node {node.node_id} from existing sub-plan.",
                data={
                    "node_id": node.node_id,
                    "sub_plan_id": sub_plan.plan_id,
                    "plan_status": sub_plan.status.value,
                },
            )
            self._enqueue_completed(envelope, graph, node.node_id, "waiting_for_approval")
            return

        if sub_plan.status in {
            PlanStatus.FAILED,
            PlanStatus.CANCELLED,
            PlanStatus.REPLAN_REQUIRED,
        }:
            self._trace(
                event,
                "subagent.resumed",
                f"Resumed failed subagent node {node.node_id} from existing sub-plan.",
                data={
                    "node_id": node.node_id,
                    "sub_plan_id": sub_plan.plan_id,
                    "plan_status": sub_plan.status.value,
                },
            )
            self._enqueue_completed(envelope, graph, node.node_id, "failed")
            return

        self._trace(
            event,
            "subagent.in_flight",
            f"Subagent node {node.node_id} already has an in-flight sub-plan.",
            data={
                "node_id": node.node_id,
                "sub_plan_id": sub_plan.plan_id,
                "plan_status": sub_plan.status.value,
            },
        )

    def _enqueue_action(
        self,
        envelope: EventEnvelope,
        sub_plan: AgentPlan,
        action: PlannedAction,
    ) -> AgentActionEvent:
        action_event = AgentActionEvent(
            workspace_id=sub_plan.workspace_id,
            project_id=sub_plan.project_id,
            plan_id=sub_plan.plan_id,
            action_id=action.action_id,
            correlation_id=envelope.event.correlation_id,
            causality=envelope.event.causality.child(envelope.event_id),
        )
        self.queue.enqueue(EventEnvelope.wrap(action_event))
        return action_event

    # -- completion / join -------------------------------------------------

    def _handle_completed(self, envelope: EventEnvelope, event: SubAgentCompletedEvent) -> None:
        run_id = event.plan_id

        if event.status not in ("completed", "failed"):
            # waiting_for_approval (or any non-terminal settle): pause the run without
            # advancing the graph.
            self._trace(
                event,
                "orchestration.waiting",
                f"Run {run_id} paused at node {event.node_id} ({event.status}).",
                data={"node_id": event.node_id, "status": event.status},
            )
            return

        status = SubTaskStatus.COMPLETED if event.status == "completed" else SubTaskStatus.FAILED
        advance = self.state_store.advance_task_graph(
            event.workspace_id, run_id, event.node_id, status
        )
        if advance is None:
            self._trace(event, "orchestration.graph_missing", f"No task graph for run {run_id}.")
            return
        if advance.already_processed:
            self._trace(
                event,
                "orchestration.duplicate",
                f"Ignored duplicate completion for node {event.node_id}.",
                data={"node_id": event.node_id, "status": event.status},
            )
            return

        if status == SubTaskStatus.COMPLETED and not advance.is_synthesizer:
            self._store_result(event.workspace_id, run_id, event.node_id)

        if advance.run_failed:
            message = str(event.payload.get("message") or "subagent_failed")
            self.agent_run_sync.record_run_failed(event.workspace_id, run_id, message)
            self._trace(
                event,
                "orchestration.failed",
                f"Run {run_id} failed at node {event.node_id}: {message}",
                data={"node_id": event.node_id},
            )
            return

        for node in advance.newly_ready:
            self._dispatch_node(envelope, advance.graph, node)

        if advance.dispatch_synthesizer is not None:
            self._dispatch_node(envelope, advance.graph, advance.dispatch_synthesizer)

        if advance.run_completed:
            self.agent_run_sync.record_run_completed_by_id(event.workspace_id, run_id)
            self._trace(
                event,
                "orchestration.completed",
                f"Run {run_id} completed.",
                data={"node_id": event.node_id},
            )

        if advance.blocked:
            self.agent_run_sync.record_run_failed(
                event.workspace_id, run_id, "orchestration_blocked"
            )
            self._trace(
                event,
                "orchestration.blocked",
                f"Run {run_id} blocked: no runnable node and not all complete.",
                data={"node_id": event.node_id},
            )

    # -- helpers -----------------------------------------------------------

    def _dispatch_node(self, envelope: EventEnvelope, graph: TaskGraph, node: SubTask) -> None:
        task_event = SubAgentTaskEvent(
            workspace_id=graph.workspace_id,
            project_id=graph.project_id,
            plan_id=graph.plan_id,
            node_id=node.node_id,
            correlation_id=envelope.event.correlation_id,
            causality=envelope.event.causality.child(envelope.event_id),
        )
        self.queue.enqueue(EventEnvelope.wrap(task_event))
        self._trace(
            envelope.event,
            "subagent.dispatched",
            f"Dispatched subagent node {node.node_id}.",
            data={"node_id": node.node_id, "dispatch_event_id": task_event.event_id},
        )

    def _enqueue_completed(
        self, envelope: EventEnvelope, graph: TaskGraph, node_id: str, status: str
    ) -> None:
        completed_event = SubAgentCompletedEvent(
            workspace_id=graph.workspace_id,
            project_id=graph.project_id,
            plan_id=graph.plan_id,
            node_id=node_id,
            status=status,
            correlation_id=envelope.event.correlation_id,
            causality=envelope.event.causality.child(envelope.event_id),
        )
        self.queue.enqueue(EventEnvelope.wrap(completed_event))

    def _store_result(self, workspace_id: str, run_id: str, node_id: str) -> None:
        sub_plan = self.state_store.get_plan(workspace_id, sub_plan_id(run_id, node_id))
        self.state_store.save_sub_agent_result(
            workspace_id, run_id, self._build_result(node_id, sub_plan)
        )

    def _build_result(self, node_id: str, sub_plan: AgentPlan | None) -> SubAgentResult:
        if sub_plan is None:
            return SubAgentResult(node_id=node_id)
        outputs: dict[str, object] = {}
        for action in sub_plan.actions:
            result = self.state_store.get_action_result(sub_plan.plan_id, action.action_id)
            if result is not None:
                outputs[action.tool_name] = result.output
        return SubAgentResult(
            node_id=node_id,
            skill_ids=list(sub_plan.skill_ids),
            summary=f"{node_id}: completed {len(outputs)} action(s).",
            structured_output={"outputs": outputs},
        )

    def _inject_results(self, context, workspace_id: str, run_id: str):
        results = self.state_store.get_sub_agent_results(workspace_id, run_id)
        entities = {
            **context.entities,
            SUBAGENT_RESULTS_KEY: [result.model_dump(mode="json") for result in results],
        }
        return context.model_copy(update={"entities": entities})

    @staticmethod
    def _is_synthesizer(graph: TaskGraph, node: SubTask) -> bool:
        return graph.synthesizer_node is not None and node.node_id == graph.synthesizer_node.node_id

    def _trace(
        self,
        event: RuntimeEvent,
        event_name: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        self.state_store.append_trace_event(
            TraceEvent(
                event_name=event_name,
                workspace_id=event.workspace_id,
                project_id=event.project_id,
                plan_id=event.plan_id if hasattr(event, "plan_id") else None,
                message=message,
                data=data or {},
            )
        )
