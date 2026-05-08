from domain.events import DomainEvent, ManualInvocationEvent
from domain.policies import PMTriggerPolicy


def test_trigger_policy_allows_meaningful_domain_event() -> None:
    decision = PMTriggerPolicy().evaluate(
        DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
    )

    assert decision.allowed is True
    assert decision.trigger_key == "domain.story.created"


def test_trigger_policy_rejects_low_value_event() -> None:
    decision = PMTriggerPolicy().evaluate(
        DomainEvent(event_type="comment.created", workspace_id="w1", project_id="p1")
    )

    assert decision.allowed is False
    assert decision.reason == "ignored_low_value_event"


def test_trigger_policy_allows_manual_invocation() -> None:
    decision = PMTriggerPolicy().evaluate(
        ManualInvocationEvent(event_type="decompose_story", workspace_id="w1", project_id="p1")
    )

    assert decision.allowed is True
    assert decision.trigger_key == "manual.decompose_story"
