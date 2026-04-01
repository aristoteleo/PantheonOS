import os

# Prevent litellm from making blocking network calls to GitHub on startup
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

__all__ = ["Repl"]


def __getattr__(name: str):
    if name == "Repl":
        from .core import Repl
        return Repl
    raise AttributeError(name)
