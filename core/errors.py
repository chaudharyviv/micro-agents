"""Exception types shared across core modules."""


class LLMUnavailableError(Exception):
    """Raised when the LLM backend can't serve a request (unconfigured, failing, or over budget)."""


class BudgetExceededError(LLMUnavailableError):
    """
    Raised, before any API call is made, when a usage limit has been reached.

    Subclasses LLMUnavailableError so agents that already turn LLM failures into a user-facing
    error message do the same here, with `str(e)` explaining which limit was hit. `scope` is one of
    "request", "client", "hourly" or "daily".
    """

    def __init__(self, message: str, scope: str):
        super().__init__(message)
        self.scope = scope


class LLMOutputError(LLMUnavailableError):
    """
    The model answered, but not with a usable result: it refused, or its output was cut off.

    Retrying the same request wouldn't help, so callers don't. `str(e)` is safe to show to users.
    """
