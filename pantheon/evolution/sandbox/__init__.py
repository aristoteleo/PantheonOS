"""Run evolution mutations inside isolated Modal sandboxes.

The mutation coding agent (which can run arbitrary shell/python) is confined to a Modal
Sandbox whose filesystem is only its own container — it physically cannot reach the host
repo, prior solutions, or secrets. This also makes mutations trivially parallel (one
sandbox per mutation).

- ``mutation_worker.py`` runs INSIDE the sandbox (uses the image's pantheon). It is uploaded
  as source, not imported — so the system prompt, objective, evaluator, and parent code are
  all injected at runtime (nothing is baked into the image).
- ``runner.py`` is the controller side: create a sandbox, inject inputs + the worker, run it,
  read back the child, tear down.
"""

from .runner import run_mutation_in_sandbox

__all__ = ["run_mutation_in_sandbox"]
