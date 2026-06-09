from __future__ import annotations

from application.agent_run_sync import AgentRunSync
from application.context_provider import ContextProvider
from application.event_router import EventRouter
from application.orchestrator import Orchestrator
from application.planner import Planner
from application.subagent_runner import SubAgentRunner
from application.trace_data import action_trace_data, event_trace_data, plan_trace_data
from domain.agent_run import resolve_agent_run_id
from domain.context import ContextSnapshot
from domain.events import AgentActionEvent, EventEnvelope, SubAgentTaskEvent, utc_now
from domain.plans import PlanStatus
from domain.results import TraceEvent
from infrastructure.queue.base import Queue
from infrastructure.registries.workflow_registry import WorkflowDefinition
from infrastructure.state.base import StateStore


class DomainEventHandler:
    def __init__(
        self,
        router: EventRouter,
        context_provider: ContextProvider,
        planner: Planner,
        state_store: StateStore,
        queue: Queue,
        agent_run_sync: AgentRunSync,
        orchestrator: Orchestrator,
        subagent_runner: SubAgentRunner,
    ) -> None:
        self.router = router
        self.context_provider = context_provider
        self.planner = planner
        self.state_store = state_store
        self.queue = queue
        self.agent_run_sync = agent_run_sync
        self.orchestrator = orchestrator
        self.subagent_runner = subagent_runner

    def handle(self, envelope: EventEnvelope) -> None:
        route = self.router.route(envelope)
        if not route.decision.allowed or route.workflow is None:
            self._trace(
                envelope,
                "event.ignored",
                f"Ignored event: {route.decision.reason}",
                {
                    **event_trace_data(envelope.event),
                    "trigger_key": route.decision.trigger_key,
                    "reason": route.decision.reason,
                },
            )
            return

        snapshot = self.context_provider.hydrate(envelope, route.workflow)
        agent_run_id = resolve_agent_run_id(snapshot.context)

        orchestration = route.workflow.orchestration
        if orchestration is not None and orchestration.default_graph:
            self._handle_orchestrated(envelope, route.workflow, snapshot, agent_run_id)
            return

        existing_plan = self.state_store.get_plan(snapshot.context.workspace_id, agent_run_id)
        should_replan = (
            existing_plan is not None
            and existing_plan.status == PlanStatus.REPLAN_REQUIRED
            and _replan_requested(envelope)
        )
        if existing_plan is not None and not should_replan:
            plan = existing_plan
            snapshot = snapshot.model_copy(update={"plan_id": plan.plan_id})
            self.state_store.save_context_snapshot(snapshot)
            self._trace(
                envelope,
                "plan.resumed",
                f"Resumed plan {plan.plan_id} for workflow {route.workflow.workflow_id}",
                plan_trace_data(plan, snapshot, route.workflow),
                plan_id=plan.plan_id,
            )
        else:
            if should_replan:
                self.agent_run_sync.record_run_running(snapshot.context.workspace_id, agent_run_id)
                self._trace(
                    envelope,
                    "plan.replanning",
                    f"Replanning run {agent_run_id} for workflow {route.workflow.workflow_id}",
                    {
                        **event_trace_data(envelope.event),
                        "previous_plan_status": existing_plan.status.value
                        if existing_plan is not None
                        else None,
                    },
                    plan_id=agent_run_id,
                )
            else:
                self.agent_run_sync.record_run_started(
                    snapshot.context,
                    agent_run_id,
                    objective=route.workflow.goal,
                    prompt_version=route.workflow.prompt_version,
                )
            try:
                task_graph = self.orchestrator.build_task_graph(snapshot.context, route.workflow)
                task_graph = task_graph.model_copy(update={"plan_id": agent_run_id})
                self.state_store.save_task_graph(task_graph)
                root_node = task_graph.nodes[0]
                plan = self.subagent_runner.plan_for_node(
                    root_node,
                    snapshot.context,
                    snapshot.ref,
                    route.workflow,
                    agent_run_id,
                )
            except Exception as exc:
                self.agent_run_sync.record_run_failed(
                    snapshot.context.workspace_id,
                    agent_run_id,
                    str(exc),
                )
                raise
            snapshot = snapshot.model_copy(update={"plan_id": plan.plan_id})
            self.state_store.save_context_snapshot(snapshot)
            self.state_store.save_plan(plan)
            self.agent_run_sync.record_plan_created(plan, snapshot.context)
            self._trace(
                envelope,
                "plan.replanned" if should_replan else "plan.created",
                (
                    f"Replanned run {plan.plan_id} for workflow {route.workflow.workflow_id}"
                    if should_replan
                    else f"Created plan {plan.plan_id} for workflow {route.workflow.workflow_id}"
                ),
                plan_trace_data(plan, snapshot, route.workflow),
                plan_id=plan.plan_id,
            )

        first_action = plan.next_pending_action()
        if first_action is None:
            if plan.status in {
                PlanStatus.COMPLETED,
                PlanStatus.FAILED,
                PlanStatus.CANCELLED,
                PlanStatus.WAITING_FOR_APPROVAL,
                PlanStatus.REPLAN_REQUIRED,
            }:
                self._trace(
                    envelope,
                    "plan.no_pending_actions",
                    f"Plan {plan.plan_id} has no executable actions in status {plan.status.value}.",
                    plan_trace_data(plan, snapshot, route.workflow),
                    plan_id=plan.plan_id,
                )
                return
            failure_message = "Planner produced no executable actions after retry attempts."
            failed_plan = plan.model_copy(
                update={"status": PlanStatus.FAILED, "updated_at": utc_now()}
            )
            self.state_store.update_plan(failed_plan)
            self.agent_run_sync.record_run_failed(
                failed_plan.workspace_id,
                failed_plan.plan_id,
                failure_message,
            )
            self._trace(
                envelope,
                "plan.empty",
                "Planner produced no executable actions.",
                plan_trace_data(failed_plan, snapshot, route.workflow),
                plan_id=failed_plan.plan_id,
            )
            self._trace(
                envelope,
                "plan.failed",
                failure_message,
                plan_trace_data(failed_plan, snapshot, route.workflow),
                plan_id=failed_plan.plan_id,
            )
            return

        action_event = AgentActionEvent(
            workspace_id=plan.workspace_id,
            project_id=plan.project_id,
            plan_id=plan.plan_id,
            action_id=first_action.action_id,
            correlation_id=envelope.event.correlation_id,
            causality=envelope.event.causality.child(envelope.event_id),
        )
        self.queue.enqueue(EventEnvelope.wrap(action_event))
        self._trace(
            envelope,
            "action.enqueued",
            f"Enqueued first action {first_action.action_id}",
            action_trace_data(
                first_action,
                {
                    "queue_event_id": action_event.event_id,
                    "queue_event_type": action_event.event_type,
                },
            ),
            plan_id=plan.plan_id,
            action_id=first_action.action_id,
        )

    def _handle_orchestrated(
        self,
        envelope: EventEnvelope,
        workflow: WorkflowDefinition,
        snapshot: ContextSnapshot,
        agent_run_id: str,
    ) -> None:
        workspace_id = snapshot.context.workspace_id
        if self.state_store.get_task_graph(workspace_id, agent_run_id) is not None:
            self._trace(
                envelope,
                "orchestration.resumed",
                f"Run {agent_run_id} already orchestrated; ignoring duplicate trigger.",
                {**event_trace_data(envelope.event)},
                plan_id=agent_run_id,
            )
            return

        graph = self.orchestrator.build_orchestrated_graph(snapshot.context, workflow)
        graph = graph.model_copy(
            update={"plan_id": agent_run_id, "context_snapshot_ref": snapshot.ref}
        )
        snapshot = snapshot.model_copy(update={"plan_id": agent_run_id})
        self.state_store.save_context_snapshot(snapshot)
        self.state_store.save_task_graph(graph)
        self.agent_run_sync.record_run_started(
            snapshot.context,
            agent_run_id,
            objective=workflow.goal,
            prompt_version=workflow.prompt_version,
        )
        self._trace(
            envelope,
            "orchestration.started",
            f"Orchestrating {len(graph.nodes)} node(s) for workflow {workflow.workflow_id}.",
            {
                **event_trace_data(envelope.event),
                "node_ids": [node.node_id for node in graph.nodes],
                "synthesizer": graph.synthesizer_node.node_id
                if graph.synthesizer_node is not None
                else None,
            },
            plan_id=agent_run_id,
        )

        ready = self.state_store.claim_ready_nodes(workspace_id, agent_run_id)
        if not ready:
            message = "Orchestration produced no runnable node."
            self.agent_run_sync.record_run_failed(workspace_id, agent_run_id, message)
            self._trace(
                envelope,
                "orchestration.empty",
                message,
                {"node_ids": [node.node_id for node in graph.nodes]},
                plan_id=agent_run_id,
            )
            return

        for node in ready:
            task_event = SubAgentTaskEvent(
                workspace_id=workspace_id,
                project_id=graph.project_id,
                plan_id=agent_run_id,
                node_id=node.node_id,
                correlation_id=envelope.event.correlation_id,
                causality=envelope.event.causality.child(envelope.event_id),
            )
            self.queue.enqueue(EventEnvelope.wrap(task_event))
            self._trace(
                envelope,
                "subagent.dispatched",
                f"Dispatched subagent node {node.node_id}.",
                {"node_id": node.node_id, "dispatch_event_id": task_event.event_id},
                plan_id=agent_run_id,
            )

    def _trace(
        self,
        envelope: EventEnvelope,
        event_name: str,
        message: str,
        data: dict | None = None,
        plan_id: str | None = None,
        action_id: str | None = None,
    ) -> None:
        self.state_store.append_trace_event(
            TraceEvent(
                event_name=event_name,
                workspace_id=envelope.event.workspace_id,
                project_id=envelope.event.project_id,
                plan_id=plan_id,
                action_id=action_id,
                message=message,
                data=data or {},
            )
        )


def _replan_requested(envelope: EventEnvelope) -> bool:
    return isinstance(envelope.event.payload.get("_agent_replan"), dict)
