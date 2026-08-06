"""What an individual *is*, independent of how it is searched for.

The existing loop can only evolve one thing: a `CodebaseSnapshot`. That is baked into `Program`,
and it is the first thing that has to give, because the algorithms we want next evolve other
material -- an idea before its implementation, a falsifier that attacks a method, a gene list with
no code in it at all.

A genome only has to answer three questions: what does it hash to (dedup), how is it shown to a
model (prompting), and how is it handed to an evaluator (measurement). Everything else -- diffing,
workspaces, file layout -- belongs to a particular genome kind, not to the abstraction.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class Genome(Protocol):
    """The evolvable material itself."""

    kind: str
    """Which population this belongs to: "code", "idea", "falsifier", ... Methods route on it,
    and the driver uses it to pick an evaluator."""

    def content_hash(self) -> str:
        """Stable hash of the content, for dedup. Two genomes with the same hash are the same
        individual as far as search is concerned, however they were produced."""
        ...

    def render(self) -> str:
        """The genome as text, for putting in a prompt."""
        ...

    def to_payload(self) -> Dict[str, Any]:
        """What the evaluator receives. For code this is a file map; for an idea, its text."""
        ...


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


@dataclass
class CodeGenome:
    """A multi-file codebase. Wraps the existing `CodebaseSnapshot` rather than replacing it, so
    the file/diff/workspace machinery and everything that already imports it keep working."""

    files: Dict[str, str] = field(default_factory=dict)
    base_path: str = ""
    kind: str = "code"

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "CodeGenome":
        return cls(files=dict(snapshot.files), base_path=getattr(snapshot, "base_path", ""))

    def to_snapshot(self) -> Any:
        from ..program import CodebaseSnapshot

        return CodebaseSnapshot(files=dict(self.files), base_path=self.base_path)

    def content_hash(self) -> str:
        return _hash(*(f"{p}\n{self.files[p]}" for p in sorted(self.files)))

    def render(self) -> str:
        return "\n\n".join(
            f"--- {p} ---\n{self.files[p]}" for p in sorted(self.files)
        )

    def to_payload(self) -> Dict[str, Any]:
        return {"files": dict(self.files)}


@dataclass
class TextGenome:
    """Free text: a hypothesis, a design, a falsifier description, a plan.

    `kind` is not fixed to "idea" because the same representation serves several populations, and
    a method that runs two text populations against each other needs to tell them apart.
    """

    text: str = ""
    kind: str = "idea"
    meta: Dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        return _hash(self.kind, self.text.strip())

    def render(self) -> str:
        return self.text

    def to_payload(self) -> Dict[str, Any]:
        return {"text": self.text, "meta": dict(self.meta)}


@dataclass
class ItemsGenome:
    """An unordered selection from a fixed universe -- a gene panel, a feature subset, a schedule.

    Present because the panel work evolved exactly this and had to smuggle it through a code
    genome ("non-code genome mode"). Order is not meaningful, so the hash sorts.
    """

    items: tuple = ()
    kind: str = "items"

    def content_hash(self) -> str:
        return _hash(self.kind, "\n".join(sorted(map(str, self.items))))

    def render(self) -> str:
        return "\n".join(map(str, self.items))

    def to_payload(self) -> Dict[str, Any]:
        return {"items": list(self.items)}
