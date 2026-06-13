"""Tests for the journal layer (Task 2): order-independent prefix-cache resume.

Addressing is ALWAYS by ``node_id`` (engine-assigned monotonic int), never by
``label``. The journal powers cheap ``resume`` via a longest-unchanged-prefix
cache: the first miss invalidates the cache for that position and everything
after it.

Storage is a ``dict[int, JournalEntry]`` keyed by node_id, so record and lookup
tolerate ANY order (concurrent completion under parallel/pipeline).
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


def _ids(journal):
    """node_ids present, ascending (dict storage -> sort keys)."""
    return sorted(journal.entries)


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
    assert _ids(reloaded) == [0]


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
    assert _ids(reloaded) == [0, 1]


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


# --- Test 9: invalidate -> record re-run path (retry flow) ---

def test_invalidate_then_record_rerun(tmp_path):
    path = _record_chain(tmp_path, ["k0", "k1", "k2"])
    j = Journal(path)
    j.invalidate(1)  # truncates entries 1, 2
    assert _ids(j) == [0]

    # resume re-runs and appends a fresh entry at the now-vacant node_id 1.
    j.record(_entry(1, "k1_new", result_ref="context/n1.json"))
    assert _ids(j) == [0, 1]
    assert j.entries[1].key == "k1_new"

    # fresh reload reproduces the re-run state exactly (last-line-wins on disk)
    reloaded = Journal(path)
    assert _ids(reloaded) == [0, 1]
    assert reloaded.entries[1].key == "k1_new"


# --- Test 10 (NEW): out-of-order record ---

def test_out_of_order_record(tmp_path):
    """Records may arrive in any order (concurrent completion under parallel)."""
    path = _journal_path(tmp_path)
    j = Journal(path)
    # Record 2, then 0, then 1 — NOT ascending. No strict-append check now.
    j.record(_entry(2, "k2", result_ref="context/n2.json"))
    j.record(_entry(0, "k0", result_ref="context/n0.json"))
    j.record(_entry(1, "k1", result_ref="context/n1.json"))

    # all three retrievable by node_id
    assert j.entries[0].key == "k0"
    assert j.entries[1].key == "k1"
    assert j.entries[2].key == "k2"
    assert _ids(j) == [0, 1, 2]

    # reload reproduces (on-disk JSONL is re-sorted into the dict by node_id)
    reloaded = Journal(path)
    assert _ids(reloaded) == [0, 1, 2]
    assert reloaded.entries[0].key == "k0"
    assert reloaded.entries[1].key == "k1"
    assert reloaded.entries[2].key == "k2"
    # the on-disk lines preserve append (completion) order: 2, 0, 1
    disk_ids = [
        json.loads(line)["node_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert disk_ids == [2, 0, 1]


# --- Test 11 (NEW): within-run duplicate record is a bug -> raises ---

def test_duplicate_record_same_run_raises(tmp_path):
    path = _journal_path(tmp_path)
    j = Journal(path)
    j.record(_entry(0, "k0", result_ref="context/n0.json"))
    try:
        j.record(_entry(0, "k0_again", result_ref="context/n0.json"))
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError on duplicate record")


# --- Test 12 (NEW): out-of-order lookup safety ---

def test_out_of_order_lookup_safety(tmp_path):
    """Lookups arriving as 2,0,1 where node 1 is a miss.

    Documents the order-independent boundary behavior:
      * node 0 hits (below any miss boundary, key matches),
      * node 1 misses (key changed) -> lowers _first_miss to 1,
      * node 2: looked up FIRST (before 1's miss is observed) -> it HITS,
        because at that moment no miss boundary <= 2 exists yet. This is SAFE:
        real upstream changes are captured by content keys, not lookup order.
    """
    path = _record_chain(tmp_path, ["k0", "k1", "k2"])
    j = Journal(path)

    # node 2 looked up FIRST, with its real key -> hits (no boundary yet).
    assert j.lookup(2, "k2") is not None
    # node 0 -> hits.
    assert j.lookup(0, "k0") is not None
    # node 1 key CHANGED -> miss, lowers _first_miss to 1.
    assert j.lookup(1, "CHANGED") is None
    assert j._first_miss == 1

    # A subsequent lookup at id >= 1 now cascades to a miss.
    assert j.lookup(2, "k2") is None


def test_out_of_order_lookup_boundary_drops_for_later(tmp_path):
    """If the low miss is observed BEFORE the high lookup, the high one misses.

    This is the conservative-cascade direction: lookup 1 (miss) then 2 -> 2
    misses because the boundary already dropped to 1.
    """
    path = _record_chain(tmp_path, ["k0", "k1", "k2"])
    j = Journal(path)
    assert j.lookup(0, "k0") is not None
    assert j.lookup(1, "CHANGED") is None  # boundary -> 1
    assert j.lookup(2, "k2") is None  # cascade


# --- Test 13 (NEW): out-of-order record then lookup roundtrip ---

def test_out_of_order_record_then_lookup(tmp_path):
    path = _journal_path(tmp_path)
    j = Journal(path)
    j.record(_entry(1, "k1", result_ref="context/n1.json"))
    j.record(_entry(0, "k0", result_ref="context/n0.json"))

    reloaded = Journal(path)
    assert reloaded.lookup(0, "k0") is not None
    assert reloaded.lookup(1, "k1") is not None


# --- recorded_node_ids accessor (I-4) ---

def test_recorded_node_ids_reflects_this_run(tmp_path):
    path = _journal_path(tmp_path)
    j = Journal(path)
    assert j.recorded_node_ids() == frozenset()
    j.record(_entry(0, "k0", result_ref="context/n0.json"))
    j.record(_entry(1, "k1", result_ref="context/n1.json"))
    assert j.recorded_node_ids() == frozenset({0, 1})

    # A freshly-loaded journal sees the persisted entries but recorded nothing
    # THIS run (cache-hit candidates, not re-executed).
    reloaded = Journal(path)
    assert reloaded.entries.keys() == {0, 1}
    assert reloaded.recorded_node_ids() == frozenset()

    # Returned set is a copy: mutating it must not affect the journal.
    snap = reloaded.recorded_node_ids()
    assert isinstance(snap, frozenset)
