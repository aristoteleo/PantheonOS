"""The unit of work the driver executes, and what comes back.

Two decisions here are the whole point of the module, and both were forced by algorithms the
current loop cannot express.

**A work item is not always a new individual.** Cascade evaluation, successive halving and
adversarial co-evolution all need "measure this existing individual again, harder" -- against more
falsifiers, at a bigger budget, on the full data rather than a subsample. `Remeasure` says that;
a design where every item creates a child cannot.

**What comes back is a measurement, not a fitness.** `Program.fitness_score()` today folds raw
metrics into one number using weights the evaluator supplied. That works only while fitness is a
fixed property of an individual. It is not: under a co-evolving adversary an individual's standing
changes when the *opponent* changes, and under Pareto selection there is no single number at all.
So the driver reports what it observed and the method decides what that is worth.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .genome import Genome


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@dataclass
class PromptContext:
    """Everything a variator is told about how to build this child.

    The method fills it in, not the driver. SimpleTES calls this the Φ lever and gets six distinct
    algorithms out of varying it alone, so it cannot live in a shared prompt builder owned by the
    loop: which ancestors, which siblings, which failures and in what order *is* the algorithm.
    """

    instruction: str = ""
    parents: List[Any] = field(default_factory=list)
    inspirations: List[Any] = field(default_factory=list)
    history: str = ""
    failures: Dict[str, float] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Create:
    """Build `k` new individuals of `kind` from `parent_ids`.

    `k > 1` is one prompt producing several candidates, which is how best-of-K commit works: the
    method sees all of them and keeps what it wants. The variator may return fewer -- generation
    fails often enough that the shortfall has to be a reported event, not a silent gap.

    `anchor_id` is the second edge in the lineage graph, and the reason `parent_ids` is not enough.
    A code individual has a code parent AND the idea it implements; a falsifier has a falsifier
    parent AND the method it was written to break. `parent_ids` is descent, `anchor_id` is what it
    is *about*.
    """

    kind: str
    parent_ids: List[str] = field(default_factory=list)
    anchor_id: Optional[str] = None
    k: int = 1
    context: PromptContext = field(default_factory=PromptContext)
    batch_id: str = field(default_factory=lambda: new_id("b"))
    fidelity: str = "full"
    meta: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("w"))


@dataclass
class Remeasure:
    """Measure an individual that already exists, at a different fidelity.

    Nothing is created. The method is buying more information about something it already has --
    promoting a cascade survivor, re-scoring an elite against a falsifier archive that has since
    grown, or spending the last of a budget confirming the incumbent.
    """

    individual_id: str
    fidelity: str = "full"
    batch_id: str = field(default_factory=lambda: new_id("b"))
    meta: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("w"))

    @property
    def kind(self) -> str:
        return "remeasure"


WorkItem = Any  # Create | Remeasure -- a Union alias kept loose for 3.9 compatibility


@dataclass
class Measurement:
    """One evaluation of one individual, at one fidelity.

    Individuals keep a list of these rather than a single `metrics` dict, because the same
    individual measured cheaply and measured properly are two different observations and the
    method may care about both -- the gap between them is the signal that a cheap proxy is
    misleading.
    """

    individual_id: str
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    fidelity: str = "full"
    ok: bool = True
    cost: float = 0.0
    duration: float = 0.0
    item_id: str = ""
    batch_id: str = ""
    at: float = field(default_factory=time.time)


@dataclass
class Failure:
    """A work item that died before producing a measurement.

    Reported rather than dropped: a method that launched a batch of k and is waiting for k answers
    will wait forever otherwise. `stage` says where it died, because the two cases mean different
    things -- "the model would not write it" is about the prompt, "it crashed when run" is about
    the code.
    """

    item_id: str
    batch_id: str = ""
    stage: str = "generate"     # "generate" | "evaluate"
    reason: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)


@dataclass
class Produced:
    """A genome the variator built for a `Create`.

    `measurement` is normally empty and the loop measures the child. A variator sets it when the
    child was *already* measured where it was made -- the sandbox operator evaluates inside the
    sandbox precisely so that evolved code never runs on the host, and re-measuring it locally
    would throw that guarantee away to recompute a number it already has.
    """

    genome: Genome
    item_id: str
    batch_id: str = ""
    parent_ids: List[str] = field(default_factory=list)
    anchor_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    measurement: Optional["Measurement"] = None
