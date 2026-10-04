"""Shared control errors without loading model inference transports."""


class ControlError(RuntimeError):
    def __init__(self, status, detail=None):
        self.status = status
        super().__init__(detail if isinstance(detail, str) and len(detail) <= 500 else
                         f'Model Services control plane returned HTTP {status}; refresh or update Hub/Fleet')
