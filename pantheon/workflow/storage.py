"""Directory layout and IO for the dynamic workflow module.

Layout per workflow::

    {base_dir}/.pantheon/workflows/{workflow_id}/
        workflow.py
        meta.json
        state.json
        journal.jsonl
        context/
            inputs.json
            n{node_id}.json
        nodes/
            n{node_id}.jsonl

Node artifact / memory filenames use ONLY the engine ``node_id`` — never
``label`` (which may contain ``/`` or ``..`` in dynamic fan-out).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from .models import WorkflowMeta, WorkflowState

WORKFLOWS_SUBDIR = ".pantheon/workflows"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically via a same-dir temp + os.replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)  # atomic on the same filesystem


def safe_node_path(workflow_dir: Path, *parts: str) -> Path:
    """Join ``parts`` under ``workflow_dir`` with path confinement.

    Each part must be a plain path segment (no ``/``, no ``..``, not absolute).
    The resolved result must remain inside the resolved ``workflow_dir``.
    Raises ``ValueError`` on any attempt to escape.
    """
    root = Path(workflow_dir).resolve()
    for part in parts:
        if part in ("", ".", ".."):
            raise ValueError(f"unsafe path part: {part!r}")
        if "/" in part or "\\" in part:
            raise ValueError(f"path part may not contain separators: {part!r}")
        if Path(part).is_absolute():
            raise ValueError(f"path part may not be absolute: {part!r}")
    candidate = root.joinpath(*parts).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes workflow dir: {candidate}")
    return candidate


class WorkflowStorage:
    """Filesystem IO for workflows rooted under ``base_dir``."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    # --- directory helpers ---

    @property
    def workflows_root(self) -> Path:
        return self.base_dir / WORKFLOWS_SUBDIR

    def workflow_dir(self, workflow_id: str) -> Path:
        return self.workflows_root / workflow_id

    def context_dir(self, workflow_id: str) -> Path:
        return self.workflow_dir(workflow_id) / "context"

    def nodes_dir(self, workflow_id: str) -> Path:
        return self.workflow_dir(workflow_id) / "nodes"

    def ensure_workflow(self, workflow_id: str) -> Path:
        """Create the workflow directory layout if missing; return its path."""
        wf_dir = self.workflow_dir(workflow_id)
        (wf_dir / "context").mkdir(parents=True, exist_ok=True)
        (wf_dir / "nodes").mkdir(parents=True, exist_ok=True)
        inputs = wf_dir / "context" / "inputs.json"
        if not inputs.exists():
            inputs.write_text("{}", encoding="utf-8")
        return wf_dir

    # --- node artifact / memory paths (node_id only) ---

    def node_context_path(self, workflow_id: str, node_id: int) -> Path:
        return safe_node_path(
            self.context_dir(workflow_id), f"n{int(node_id)}.json"
        )

    def node_memory_path(self, workflow_id: str, node_id: int) -> Path:
        return safe_node_path(
            self.nodes_dir(workflow_id), f"n{int(node_id)}.jsonl"
        )

    # --- meta ---

    def meta_path(self, workflow_id: str) -> Path:
        return self.workflow_dir(workflow_id) / "meta.json"

    def write_meta(self, meta: WorkflowMeta) -> None:
        self.ensure_workflow(meta.workflow_id)
        _atomic_write_text(
            self.meta_path(meta.workflow_id),
            json.dumps(asdict(meta), indent=2, sort_keys=True),
        )

    def read_meta(self, workflow_id: str) -> WorkflowMeta:
        data = json.loads(self.meta_path(workflow_id).read_text(encoding="utf-8"))
        return WorkflowMeta(**data)

    # --- state ---

    def state_path(self, workflow_id: str) -> Path:
        return self.workflow_dir(workflow_id) / "state.json"

    def write_state(self, state: WorkflowState) -> None:
        self.ensure_workflow(state.workflow_id)
        _atomic_write_text(
            self.state_path(state.workflow_id),
            json.dumps(asdict(state), indent=2, sort_keys=True),
        )

    def read_state(self, workflow_id: str) -> WorkflowState:
        data = json.loads(self.state_path(workflow_id).read_text(encoding="utf-8"))
        return WorkflowState(**data)

    # --- script ---

    def script_path(self, workflow_id: str) -> Path:
        return self.workflow_dir(workflow_id) / "workflow.py"

    def write_script(self, workflow_id: str, source: str) -> None:
        self.ensure_workflow(workflow_id)
        self.script_path(workflow_id).write_text(source, encoding="utf-8")

    def read_script(self, workflow_id: str) -> str:
        return self.script_path(workflow_id).read_text(encoding="utf-8")

    # --- final result ---

    def result_path(self, workflow_id: str) -> Path:
        return self.workflow_dir(workflow_id) / "result.json"

    def write_result(self, workflow_id: str, value: object) -> None:
        """Atomically persist a workflow's final return value to result.json.

        Serializes ``{"result": value}`` with ``default=str``; if that still
        fails (e.g. a non-serializable container), falls back to stringifying
        the value. Uses the same temp-file + ``os.replace`` atomic write as the
        meta/state writers.
        """
        self.ensure_workflow(workflow_id)
        try:
            text = json.dumps({"result": value}, default=str)
        except (TypeError, ValueError):
            text = json.dumps({"result": str(value)})
        _atomic_write_text(self.result_path(workflow_id), text)

    def read_result(self, workflow_id: str) -> object:
        """Read back the ``{"result": ...}`` payload's ``result`` value."""
        data = json.loads(self.result_path(workflow_id).read_text(encoding="utf-8"))
        return data.get("result")


def scan_workflows(base_dir: Path) -> list[WorkflowMeta]:
    """Scan ``{base_dir}/.pantheon/workflows/*/meta.json`` for valid metas.

    Directories without a valid, parseable ``meta.json`` are skipped.
    """
    root = Path(base_dir) / WORKFLOWS_SUBDIR
    metas: list[WorkflowMeta] = []
    if not root.is_dir():
        return metas
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        meta_file = entry / "meta.json"
        if not meta_file.is_file():
            continue
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            metas.append(WorkflowMeta(**data))
        except (json.JSONDecodeError, TypeError, ValueError, OSError):
            continue
    return metas
