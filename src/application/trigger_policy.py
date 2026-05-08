from __future__ import annotations

from domain.events import BaseRuntimeEvent
from domain.policies import PMTriggerPolicy, TriggerPolicyDecision


class TriggerPolicy:
    def __init__(self, domain_policy: PMTriggerPolicy | None = None) -> None:
        self.domain_policy = domain_policy or PMTriggerPolicy()

    def evaluate(self, event: BaseRuntimeEvent) -> TriggerPolicyDecision:
        return self.domain_policy.evaluate(event)
