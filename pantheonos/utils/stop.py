"""Interrupting a run that is calling a model."""


class StopRunning(Exception):
    """Raised to interrupt a running agent.

    Optionally carries a partial assistant message (dict) that was being
    streamed when the stop was requested, so callers can persist it.
    """

    def __init__(self, partial_message: dict | None = None):
        super().__init__()
        self.partial_message = partial_message
