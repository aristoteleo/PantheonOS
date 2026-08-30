"""What is NOT an App: components and legacy aliases.

Apps define themselves — each first-party App directory under
pantheon/apps/builtin/ carries its own app.json, and the registry discovers
them by scanning (pantheon/apps/registry.py). The old CATALOG table
dissolved into those manifests.

What remains here is the residue that has no manifest by design:

  component  an internal part of another App — instantiated by its parent,
             never placed on a node by itself;
  alias      a legacy class name kept so saved agent configs still load.

The triage test (test_app_registry) uses this table plus the manifests'
entry.backend to prove every shipped ToolSet class is accounted for —
a new class cannot slip in untracked.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NonAppClass:
    class_name: str
    kind: str          # component | alias
    parent: str        # the app_id it belongs to
    notes: str = ""


NON_APP_CLASSES: tuple[NonAppClass, ...] = (
    NonAppClass("JupyterKernelToolSet", "component", "integrated-notebook"),
    NonAppClass("NotebookContentsToolSet", "component", "integrated-notebook"),
    NonAppClass("EvaluatorToolSet", "component", "evolution"),
    NonAppClass("LiveViewToolSet", "alias", "desktop",
                notes="saved agent configs may still name the old class"),
)


def non_app_class_names() -> set[str]:
    return {c.class_name for c in NON_APP_CLASSES}
