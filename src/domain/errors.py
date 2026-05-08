class PrismAgentError(Exception):
    """Base error for prism-agent runtime failures."""


class UnsupportedEventError(PrismAgentError):
    """Raised when an event is intentionally outside the PM Agent trigger scope."""


class PlanNotFoundError(PrismAgentError):
    """Raised when action execution cannot restore its persisted plan."""


class ActionNotFoundError(PrismAgentError):
    """Raised when an action event references an unknown planned action."""


class StaleContextError(PrismAgentError):
    """Raised when an action was planned from outdated entity versions."""


class DuplicateExecutionError(PrismAgentError):
    """Raised when an idempotency key has already been consumed."""
