from __future__ import annotations

from application.domain_event_handler import DomainEventHandler
from domain.events import EventEnvelope


class ScheduledEventHandler:
    def __init__(self, domain_event_handler: DomainEventHandler) -> None:
        self.domain_event_handler = domain_event_handler

    def handle(self, envelope: EventEnvelope) -> None:
        self.domain_event_handler.handle(envelope)
