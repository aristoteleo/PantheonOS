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
    # §A.8 dynamic-workflow declaration metadata. ``preview`` is an explicit
    # state: "full" (blueprint declared, trace pre-renderable) or "none"
    # (legacy script with no blueprint). ``blueprint`` is the pure-literal slot
    # declaration list: [{slot_id, phase, label, kind, schema?}].
    preview: str = "full"
    blueprint: list = field(default_factory=list)
    # §A.4 monotonic revision counter, incremented on every status transition
    # (running → awaiting_intervention → running → …). It is the CAS baseline
    # for C4 intervention: a control() carrying a stale revision is rejected.
    revision: int = 0


@dataclass
class WorkflowState:
    """Mutable runtime state, persisted to ``state.json``."""

    workflow_id: str
    status: str
    current_phase: str = ""
    progress: dict = field(default_factory=dict)  # e.g. {"total": int, "done": int}
    # §A.6 cascade epoch: a per-workflow monotonic counter bumped on every
    # ``retry_node`` invalidation cascade. Stamped onto node events so the UI
    # can drop events from a superseded epoch on reconnect.
    cascade_epoch: int = 0
    # §A.2 per-node retry counter. Keyed by str(node_id) (JSON object keys are
    # strings); value is the number of times that node_id has been (re-)run.
    # Lives here — not on JournalEntry — because ``invalidate`` DELETES the
    # entry, so the count must survive in state across the delete.
    attempt_counts: dict = field(default_factory=dict)


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
    # §A.2: slot this node filled (from ``node(slot=...)``); None if undeclared.
    slot_id: str | None = None
    # §A.2: per-node retry counter snapshot at record time (0 on first run,
    # +1 each retry). The authoritative running counter lives in
    # ``WorkflowState.attempt_counts``; this is the value that was in effect
    # when the entry was recorded, so ``status()`` can report it per node.
    attempt: int = 0


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
