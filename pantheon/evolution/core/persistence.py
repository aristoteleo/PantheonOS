"""Saving a run and picking it back up.

Two things have to survive a restart, and they are not the same kind of thing:

  the **store** is the record -- every individual and every measurement, which cost real money to
  produce and can never be recomputed

  the **method state** is derived -- an archive, a set of chains, a visit table. It could in
  principle be rebuilt from the store, but only the method knows how, which is why the protocol
  has `state_dict` and `reconcile` rather than the loop guessing

They are written separately for that reason. A method that changes shape between runs can drop its
state and reconcile against the store; the store itself is never reinterpreted.

Genomes are tagged by type on the way out and rebuilt through a registry on the way in, so a new
genome kind only has to register itself rather than teach this module about it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .genome import CodeGenome, Genome, ItemsGenome, TextGenome
from .individual import Individual, Store
from .work import Measurement

GENOME_TYPES: Dict[str, Callable[[Dict[str, Any]], Genome]] = {
    "code": lambda d: CodeGenome(files=dict(d.get("files", {})), base_path=d.get("base_path", ""),
                                 kind=d.get("kind", "code")),
    "text": lambda d: TextGenome(text=d.get("text", ""), kind=d.get("kind", "idea"),
                                 meta=dict(d.get("meta", {}))),
    "items": lambda d: ItemsGenome(items=tuple(d.get("items", ())), kind=d.get("kind", "items")),
}


def register_genome(tag: str, builder: Callable[[Dict[str, Any]], Genome]) -> None:
    GENOME_TYPES[tag] = builder


def _genome_to_dict(g: Genome) -> Dict[str, Any]:
    if isinstance(g, CodeGenome):
        return {"_type": "code", "kind": g.kind, "files": dict(g.files), "base_path": g.base_path}
    if isinstance(g, TextGenome):
        return {"_type": "text", "kind": g.kind, "text": g.text, "meta": dict(g.meta)}
    if isinstance(g, ItemsGenome):
        return {"_type": "items", "kind": g.kind, "items": list(g.items)}
    return {"_type": "text", "kind": getattr(g, "kind", "code"), "text": g.render(), "meta": {}}


def _genome_from_dict(d: Dict[str, Any]) -> Genome:
    build = GENOME_TYPES.get(d.get("_type", "text"))
    if build is None:
        raise ValueError(f"unknown genome type {d.get('_type')!r}; register_genome() it first")
    return build(d)


def _measurement_to_dict(m: Measurement) -> Dict[str, Any]:
    return {"metrics": dict(m.metrics), "artifacts": _jsonable(m.artifacts),
            "fidelity": m.fidelity, "ok": m.ok, "cost": m.cost, "duration": m.duration,
            "item_id": m.item_id, "batch_id": m.batch_id, "at": m.at}


def _jsonable(obj: Any) -> Any:
    """Artifacts come from user evaluators and may hold anything. Keep what survives a round trip
    and stringify the rest, rather than losing the whole checkpoint to one numpy array."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        if isinstance(obj, dict):
            return {str(k): _jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_jsonable(v) for v in obj]
        return repr(obj)[:2000]


def save_run(
    path: str,
    store: Store,
    method: Any,
    *,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Write the store and the method's state. Atomic per file: written aside, then renamed, so a
    crash mid-write leaves the previous checkpoint intact rather than a truncated one."""
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)

    individuals = []
    for ind in sorted(store, key=lambda i: i.order):
        individuals.append({
            "id": ind.id, "kind": ind.kind, "genome": _genome_to_dict(ind.genome),
            "parent_ids": list(ind.parent_ids), "anchor_id": ind.anchor_id,
            "generation": ind.generation, "order": ind.order,
            "meta": _jsonable(ind.meta), "created_at": ind.created_at,
            "measurements": [_measurement_to_dict(m) for m in ind.measurements],
        })

    # `run.json` is the checkpoint's own metadata and is rewritten on every save. A caller that
    # also wants to drop a run summary in this directory must not name it `run.json` -- the two
    # silently overwrite each other and whichever writes last decides whether the run can resume.
    _atomic(out / "store.json", {"version": 1, "individuals": individuals})
    _atomic(out / "method.json", {"name": getattr(method, "name", "?"),
                                  "state": _jsonable(method.state_dict())})
    _atomic(out / "run.json", {"saved_at": time.time(), "individuals": len(individuals),
                               **(meta or {})})


def _atomic(target: Path, payload: Dict[str, Any]) -> None:
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, target)


def load_run(path: str) -> Tuple[Store, Dict[str, Any], Dict[str, Any]]:
    """Return `(store, method_state, meta)`. Raises if the store is missing -- resuming without
    the record would silently start a fresh run under an old run's name."""
    p = Path(path)
    store_file = p / "store.json"
    if not store_file.exists():
        raise FileNotFoundError(f"no store.json in {path}")

    data = json.loads(store_file.read_text(encoding="utf-8"))
    store = Store()
    for row in data.get("individuals", []):
        ind = Individual(
            genome=_genome_from_dict(row["genome"]),
            id=row["id"], kind=row.get("kind", ""),
            parent_ids=list(row.get("parent_ids", [])),
            anchor_id=row.get("anchor_id"),
            generation=row.get("generation", 0),
            meta=dict(row.get("meta", {})),
            created_at=row.get("created_at", time.time()),
        )
        for md in row.get("measurements", []):
            ind.measurements.append(Measurement(individual_id=ind.id, **md))
        store.add(ind)
        # keep the original ordering: `add` assigns a fresh order, but order is referenced by
        # prompts ("#N") and by anything comparing runs
        stored = store.get(ind.id)
        if stored is not None and "order" in row:
            stored.order = row["order"]

    method_state: Dict[str, Any] = {}
    mf = p / "method.json"
    if mf.exists():
        method_state = json.loads(mf.read_text(encoding="utf-8")).get("state", {})
    meta: Dict[str, Any] = {}
    rf = p / "run.json"
    if rf.exists():
        meta = json.loads(rf.read_text(encoding="utf-8"))
    return store, method_state, meta
