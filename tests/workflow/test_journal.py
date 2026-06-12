"""Tests for the journal layer (Task 2): prefix-cache resume semantics.

Addressing is ALWAYS by ``node_id`` (engine-assigned monotonic int), never by
``label``. The journal powers cheap ``resume`` via a longest-unchanged-prefix
cache: the first miss invalidates the cache for that position and everything
after it.
"""

import json

from pantheon.workflow.models import JournalEntry
from pantheon.workflow.journal import Journal


def _entry(node_id, key, label="", status="completed", result_ref=None):
    return JournalEntry(
        node_id=node_id,
        key=key,
        label=label,
        status=status,
        result_ref=result_ref,
        token_cost=0,
    )


def _journal_path(tmp_path):
    return tmp_path / "journal.jsonl"


def _record_chain(tmp_path, keys, labels=None):
    """Record entries 0..n-1 with the given keys; return the file path."""
    path = _journal_path(tmp_path)
    j = Journal(path)
    for i, key in enumerate(keys):
        label = labels[i] if labels else ""
        j.record(_entry(i, key, label=label, result_ref=f"context/n{i}.json"))
    return path


# --- Test 1: prefix hit ---

def test_prefix_hit_all_match(tmp_path):
    path = _record_chain(tmp_path, ["k0", "k1", "k2"])
    j = Journal(path)
    assert j.lookup(0, "k0").node_id == 0
    assert j.lookup(1, "k1").node_id == 1
    assert j.lookup(2, "k2").node_id == 2


# --- Test 2: param change -> miss + cascade ---

def test_param_change_misses_and_cascades(tmp_path):
    path = _record_chain(tmp_path, ["k0", "k1", "k2", "k3"])
    j = Journal(path)
    assert j.lookup(0, "k0") is not None
    assert j.lookup(1, "k1") is not None
    # position 2 key differs -> miss
    assert j.lookup(2, "CHANGED") is None
    # position 3 stored key matches the recomputed key, but the cascade rule
    # means it must STILL miss because an earlier position missed.
    assert j.lookup(3, "k3") is None


# --- Test 3: retry (invalidate by node_id) ---

def test_invalidate_truncates_from_node_id(tmp_path):
    path = _record_chain(tmp_path, ["k0", "k1", "k2"])
    j = Journal(path)
    j.invalidate(1)
    assert j.lookup(0, "k0") is not None
    assert j.lookup(1, "k1") is None

    # reload: entries 1 and 2 are gone on disk
    reloaded = Journal(path)
    ids = [e.node_id for e in reloaded.entries]
    assert ids == [0]


# --- Test 4: skip injection (no cascade) ---

def test_skip_marks_position_and_does_not_cascade(tmp_path):
    path = _record_chain(tmp_path, ["k0", "k1", "k2"])
    j = Journal(path)
    j.skip(1)

    hit0 = j.lookup(0, "k0")
    assert hit0 is not None and hit0.status == "completed"

    # skipped position is a HIT regardless of key, status skipped, no result_ref
    skipped = j.lookup(1, "anything")
    assert skipped is not None
    assert skipped.status == "skipped"
    assert skipped.result_ref is None

    # skip does NOT cascade: position 2 still hits with its real key
    assert j.lookup(2, "k2") is not None

    # reload reflects the skip
    reloaded = Journal(path)
    e1 = reloaded.entries[1]
    assert e1.status == "skipped"
    assert e1.result_ref is None


# --- Test 5: duplicate/empty label does not affect node_id addressing ---

def test_addressing_by_node_id_not_label(tmp_path):
    # nodes 0 and 2 share an identical label; node 1 has an empty label.
    path = _record_chain(
        tmp_path,
        ["k0", "k1", "k2"],
        labels=["dup", "", "dup"],
    )
    j = Journal(path)
    # skip exactly node 1 (empty label) -> only node 1 affected
    j.skip(1)
    assert j.lookup(0, "k0").status == "completed"
    assert j.lookup(1, "k1").status == "skipped"
    assert j.lookup(2, "k2").status == "completed"

    # invalidate node 2 (shares label with node 0) -> only node 2 truncated
    j2 = Journal(path)
    j2.invalidate(2)
    reloaded = Journal(path)
    assert [e.node_id for e in reloaded.entries] == [0, 1]


# --- Test 6: crash recovery ---

def test_crash_recovery_roundtrip(tmp_path):
    path = _record_chain(
        tmp_path,
        ["k0", "k1"],
        labels=["a", "b"],
    )
    fresh = Journal(path)
    assert len(fresh.entries) == 2
    assert fresh.entries[0] == _entry(0, "k0", label="a", result_ref="context/n0.json")
    assert fresh.entries[1] == _entry(1, "k1", label="b", result_ref="context/n1.json")

    # raw file is valid JSONL, one object per line
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["node_id"] == 0


# --- Test 7: no prior entry at a position is a miss + cascade ---

def test_missing_position_is_miss(tmp_path):
    path = _record_chain(tmp_path, ["k0", "k1"])
    j = Journal(path)
    assert j.lookup(0, "k0") is not None
    # position 2 has no prior entry -> miss, and beyond is moot
    assert j.lookup(2, "k2") is None


# --- Test 8: load classmethod parity ---

def test_load_classmethod(tmp_path):
    path = _record_chain(tmp_path, ["k0"])
    j = Journal.load(path)
    assert j.lookup(0, "k0") is not None
