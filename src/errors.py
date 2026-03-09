class RagTemplateError(Exception):
    """Base error for all template failures."""


class PromptNotFoundError(RagTemplateError):
    """Raised when a prompt file or key is missing."""


class ToolExecutionError(RagTemplateError):
    """Raised when tool execution fails."""


class RetrievalError(RagTemplateError):
    """Raised when retrieval fails."""


class LlmError(RagTemplateError):
    """Raised when model invocation fails."""

