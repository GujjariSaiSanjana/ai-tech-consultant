"""Typed error chain for model calls. Callers catch the narrowest class they can handle."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every failure of a model call."""

    def __init__(self, message: str, *, agent: str | None = None) -> None:
        super().__init__(message)
        self.agent = agent


class LLMConfigError(LLMError):
    """Not retryable: bad request, auth, permission, unknown model. Fix configuration or code."""


class LLMUnavailableError(LLMError):
    """Transient: rate limit, overload, 5xx, network or timeout, after the SDK's own retries."""


class LLMRefusalError(LLMError):
    """The model declined (stop_reason == "refusal")."""

    def __init__(self, message: str, *, agent: str | None = None, category: str | None = None):
        super().__init__(message, agent=agent)
        self.category = category


class LLMTruncatedError(LLMError):
    """Output hit max_tokens or the context window before the structured answer was complete."""


class LLMSchemaError(LLMError):
    """The response did not contain a valid instance of the requested schema."""


class BudgetExceededError(LLMError):
    """The per-run spending cap was reached; no further calls are made."""
