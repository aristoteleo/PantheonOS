"""Directory refusal independent of HTTP or local storage adapters."""
class DirectoryError(ValueError):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)
