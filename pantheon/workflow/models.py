"""Data models for the dynamic workflow module.

Pure stdlib. Deterministic: never call datetime.now/time/random here —
timestamps and ids are supplied by the caller.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowMeta:
    """Static metadata for a workflow, persisted to ``meta.json``."""

    workflow_id: str
    chat_id: str
    goal: str
    created_at: str  # ISO string, passed in by caller (determinism)
    status: str  # pending/running/failed/completed/interrupted


@dataclass
class WorkflowState:
    """Mutable runtime state, persisted to ``state.json``."""

    workflow_id: str
    status: str
    current_phase: str = ""
    progress: dict = field(default_factory=dict)  # e.g. {"total": int, "done": int}


@dataclass
class NodeCall:
    """Full ``node()`` call parameters plus engine-assigned addressing.

    ``node_id`` is the engine-assigned integer id, allocated synchronously in
    source-evaluation order (structurally deterministic, not completion-order;
    ``pipeline`` reserves item-major/stage-minor blocks), and is the addressing
    key. ``label`` is display-only and must never touch a filesystem
    path.
    """

    node_id: int
    instruction: str
    template: str = "generic"
    schema: dict | None = None
    inputs: tuple[str, ...] = ()
    label: str = ""  # display only
    phase: str = ""
    model: str | None = None
    timeout: float | None = None

    def __post_init__(self) -> None:
        # Normalize inputs to a tuple so JSON round-trips (list) compare equal.
        self.inputs = tuple(self.inputs)


@dataclass
class NodeResult:
    """Outcome of executing a single node."""

    node_id: int
    status: str  # completed/failed/skipped
    result: Any = None
    result_ref: str | None = None  # relative path to context file
    error: str | None = None
    token_cost: int = 0


@dataclass
class JournalEntry:
    """A single append-only journal record (``journal.jsonl``)."""

    node_id: int
    key: str  # the node hash
    label: str
    status: str
    result_ref: str | None = None
    token_cost: int = 0


def compute_node_key(
    instruction: str,
    template: str,
    schema: dict | None,
    model: str | None,
    input_hashes: Sequence[str],
) -> str:
    """Deterministically hash the cache-relevant identity of a node.

    Folds, in order: instruction, template, canonical-json(schema),
    (model or ""), then each input file content hash in declared order.

    ``label``, ``phase``, ``node_id`` and ``timeout`` deliberately do NOT
    participate (decision 10).
    """
    h = hashlib.sha256()
    schema_json = json.dumps(schema, sort_keys=True)  # None -> "null"
    parts = [instruction, template, schema_json, model or ""]
    parts.extend(input_hashes)  # ordered: inputs is a declared ordered list
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")  # field separator to avoid ambiguity
    return h.hexdigest()
