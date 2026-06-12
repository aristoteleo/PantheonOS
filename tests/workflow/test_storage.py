"""Tests for pantheon.workflow models and directory storage (Task 1)."""

import pytest

from pantheon.workflow.models import (
    WorkflowMeta,
    WorkflowState,
    NodeCall,
    NodeResult,
    JournalEntry,
    compute_node_key,
)
from pantheon.workflow.storage import (
    WorkflowStorage,
    safe_node_path,
    scan_workflows,
)


# --- Test 1: directory creation & layout ---

def test_ensure_creates_layout(tmp_path):
    storage = WorkflowStorage(tmp_path)
    wf_dir = storage.ensure_workflow("wf1")
    assert wf_dir.is_dir()
    assert (wf_dir / "context").is_dir()
    assert (wf_dir / "nodes").is_dir()
    # context/inputs.json should exist
    assert (wf_dir / "context" / "inputs.json").exists()

    # script round trip
    storage.write_script("wf1", "print('hi')\n")
    assert storage.read_script("wf1") == "print('hi')\n"

    # path helpers
    assert storage.context_dir("wf1") == wf_dir / "context"
    assert storage.nodes_dir("wf1") == wf_dir / "nodes"


# --- Test 2: meta/state JSON round trip ---

def test_meta_roundtrip(tmp_path):
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf1")
    meta = WorkflowMeta(
        workflow_id="wf1",
        chat_id="chatA",
        goal="do the thing",
        created_at="2026-01-01T00:00:00",
        status="pending",
    )
    storage.write_meta(meta)
    loaded = storage.read_meta("wf1")
    assert loaded == meta


def test_state_roundtrip(tmp_path):
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf1")
    state = WorkflowState(
        workflow_id="wf1",
        status="running",
        current_phase="phase-1",
        progress={"total": 3, "done": 1},
    )
    storage.write_state(state)
    loaded = storage.read_state("wf1")
    assert loaded == state


# --- Test 3: compute_node_key stability ---

def test_compute_node_key_stable_and_sensitive():
    base = dict(
        instruction="summarize",
        template="generic",
        schema={"type": "object", "a": 1},
        model="gpt",
        input_hashes=("h1", "h2"),
    )
    k = compute_node_key(**base)
    # determinism
    assert k == compute_node_key(**base)
    assert isinstance(k, str) and len(k) == 64

    # changing an input hash -> different
    assert compute_node_key(**{**base, "input_hashes": ("h1", "hX")}) != k
    # input order matters
    assert compute_node_key(**{**base, "input_hashes": ("h2", "h1")}) != k

    # changing instruction/template/schema/model -> different
    assert compute_node_key(**{**base, "instruction": "other"}) != k
    assert compute_node_key(**{**base, "template": "other"}) != k
    assert compute_node_key(**{**base, "schema": {"type": "object", "a": 2}}) != k
    assert compute_node_key(**{**base, "model": "claude"}) != k

    # schema key ordering should NOT matter (canonical json sort_keys)
    assert compute_node_key(**{**base, "schema": {"a": 1, "type": "object"}}) == k

    # None schema / None model handled
    none_variant = dict(base, schema=None, model=None)
    assert compute_node_key(**none_variant) == compute_node_key(**none_variant)


def test_compute_node_key_ignores_label_phase_etc():
    # label, phase, node_id, timeout do not participate -> they're simply not args.
    # Verify two NodeCalls differing only in label produce the same key when we
    # feed the hash-relevant fields.
    call_a = NodeCall(node_id=1, instruction="x", template="t", schema={"k": 1},
                      model="m", label="label-a", phase="p1", timeout=5.0)
    call_b = NodeCall(node_id=99, instruction="x", template="t", schema={"k": 1},
                      model="m", label="label-b", phase="p2", timeout=10.0)
    ih = ("a",)
    ka = compute_node_key(call_a.instruction, call_a.template, call_a.schema,
                          call_a.model, ih)
    kb = compute_node_key(call_b.instruction, call_b.template, call_b.schema,
                          call_b.model, ih)
    assert ka == kb


# --- Test 4: safe_node_path confinement ---

def test_safe_node_path_accepts_normal(tmp_path):
    storage = WorkflowStorage(tmp_path)
    wf_dir = storage.ensure_workflow("wf1")
    p = safe_node_path(wf_dir, "context", "n5.json")
    assert p == (wf_dir / "context" / "n5.json").resolve()
    # confined
    assert str(p).startswith(str(wf_dir.resolve()))


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b", "..", "/abs/path"])
def test_safe_node_path_rejects_escapes(tmp_path, bad):
    storage = WorkflowStorage(tmp_path)
    wf_dir = storage.ensure_workflow("wf1")
    with pytest.raises(ValueError):
        safe_node_path(wf_dir, bad)


# --- Test 5: scan_workflows ---

def test_scan_workflows(tmp_path):
    storage = WorkflowStorage(tmp_path)
    for wid, goal in [("wf1", "g1"), ("wf2", "g2")]:
        storage.ensure_workflow(wid)
        storage.write_meta(WorkflowMeta(
            workflow_id=wid, chat_id="c", goal=goal,
            created_at="2026-01-01T00:00:00", status="pending",
        ))
    # an invalid dir with no meta.json
    (tmp_path / ".pantheon" / "workflows" / "broken").mkdir(parents=True)
    # a dir with malformed meta.json
    bad = tmp_path / ".pantheon" / "workflows" / "badmeta"
    bad.mkdir(parents=True)
    (bad / "meta.json").write_text("{not json")

    metas = scan_workflows(tmp_path)
    ids = sorted(m.workflow_id for m in metas)
    assert ids == ["wf1", "wf2"]
