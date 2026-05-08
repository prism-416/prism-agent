from __future__ import annotations

from dataclasses import dataclass

from application.trigger_policy import TriggerPolicy
from domain.events import EventEnvelope
from domain.policies import TriggerPolicyDecision
from infrastructure.registries.workflow_registry import WorkflowDefinition, WorkflowRegistry


@dataclass(frozen=True)
class RouteResult:
    decision: TriggerPolicyDecision
    workflow: WorkflowDefinition | None


class EventRouter:
    def __init__(self, trigger_policy: TriggerPolicy, workflow_registry: WorkflowRegistry) -> None:
        self.trigger_policy = trigger_policy
        self.workflow_registry = workflow_registry

    def route(self, envelope: EventEnvelope) -> RouteResult:
        decision = self.trigger_policy.evaluate(envelope.event)
        if not decision.allowed:
            return RouteResult(decision=decision, workflow=None)
        workflow = self.workflow_registry.get_by_trigger(decision.trigger_key)
        return RouteResult(decision=decision, workflow=workflow)
