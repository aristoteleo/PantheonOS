"""The pluggable part: an evolutionary algorithm, and the two services it is given.

The split is:

  `EvolveMethod`  decides *what to do next* and *what is good*  -- selection, admission, archive
                  shape, phase, prompt context, ranking. This is the algorithm.
  `Variator`      turns a request for a child into actual genomes -- the LLM, the diff, the
                  crossover operator. This is machinery, and it is shared.
  `Evaluator`     turns a genome into numbers.

`EvolutionTeam` today owns all three plus the loop, which is why adding a fourth algorithm means
adding a fourth `if` in three places. Here the loop owns none of them.

The callback shape (`ask` / `on_measured` / `on_failed`) is event-driven rather than batch because
evaluation is concurrent: a batch of k candidates comes back one at a time, and a method that has
to wait for all k before it can react gives up the concurrency it just paid for. SimpleTES arrived
at the same three callbacks independently, which is the main reason to trust the shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from .individual import Individual, Ranking, Store
from .work import Create, Failure, Measurement, Produced, PromptContext, WorkItem


@dataclass
class Budget:
    """What the run is allowed to spend. Methods read it; the driver enforces it."""

    max_items: Optional[int] = None
    max_cost: Optional[float] = None
    max_seconds: Optional[float] = None
    items_used: int = 0
    cost_used: float = 0.0
    seconds_used: float = 0.0

    def exhausted(self) -> bool:
        return (
            (self.max_items is not None and self.items_used >= self.max_items)
            or (self.max_cost is not None and self.cost_used >= self.max_cost)
            or (self.max_seconds is not None and self.seconds_used >= self.max_seconds)
        )

    def remaining_items(self) -> Optional[int]:
        return None if self.max_items is None else max(0, self.max_items - self.items_used)


@dataclass
class EvolveContext:
    """Read-only view of the run, handed to the method on every call."""

    store: Store
    budget: Budget
    objective: str = ""
    rng: Any = None
    in_flight: int = 0
    """Work items dispatched but not yet resolved. A method returns an empty `ask` to apply
    backpressure when it does not want more concurrency than this."""
    concurrency: int = 1
    config: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class EvolveMethod(Protocol):
    """An evolutionary algorithm."""

    name: str

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        """Called once with the seed individuals, already stored and measured."""
        ...

    async def ask(self, ctx: EvolveContext, n: int) -> List[WorkItem]:
        """Up to `n` work items to run next.

        Returning fewer, or none, is legitimate and means backpressure -- the method does not want
        more work outstanding yet. The driver will ask again when something resolves.
        """
        ...

    async def on_measured(self, ctx: EvolveContext, ind: Individual, m: Measurement) -> None:
        """One work item produced a measurement. The method decides what to do about it: admit,
        discard, update an archive, advance a phase, finish a batch."""
        ...

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        """One work item died before it could be measured.

        Not optional. A method tracking a batch of k is waiting for k answers, and generation
        failure is common enough that treating it as silence deadlocks the batch.
        """
        ...

    def rank(self, ctx: EvolveContext, kind: str = "code") -> Ranking:
        """Order the individuals of a kind. Scalar, Pareto tiers, or anything else the method
        wants -- the driver only uses this to report, never to select."""
        ...

    def done(self, ctx: EvolveContext) -> bool:
        """Stop early. Budget exhaustion is checked separately by the driver."""
        ...

    def state_dict(self) -> Dict[str, Any]:
        ...

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        ...

    def reconcile(self, ctx: EvolveContext) -> None:
        """Rebuild internal indexes against a store restored from a checkpoint. Needed because a
        method's archive is derived state and the store is the record."""
        ...

    def default_variator(self, **kw) -> Optional["Variator"]:
        """The operator this algorithm is defined with.

        Not a preference -- an identity. SimpleTES is a chain policy AND a single completion that
        cannot run anything; giving it a coding agent that verifies its own edits before
        submitting produces better numbers and is no longer SimpleTES. A method that ships without
        naming its operator lets the caller silently change what the algorithm is.

        `evolve()` uses this when no variator is passed. Passing one explicitly is for controlled
        experiments -- holding the operator fixed to compare two search policies -- and should be
        a deliberate act, not the default path.
        """
        ...

    def default_evaluators(self, **kw) -> Dict[str, "Evaluator"]:
        """The evaluators this algorithm brings with it, by kind.

        Most measurement belongs to the *problem*, not the method: the caller owns the verifier
        that scores code, and no method should be able to change what a score means. But some
        methods invent a kind and evaluate it themselves -- `AnnealedIdeaCode` judges IDEAS with
        a model whose calibration it owns and refits -- and those evaluators are part of the
        algorithm in exactly the way `default_variator` is.

        `evolve()` merges these under whatever the caller passed, so a caller-supplied kind always
        wins and the method only fills gaps. Without this seam a method that owns an evaluator has
        to hope the caller remembers to register it, and forgetting is silent: the loop reports
        `no evaluator for kind ...` per item and the search quietly runs half-blind.
        """
        ...


@runtime_checkable
class Variator(Protocol):
    """Produces genomes for a `Create`. The only component that talks to a model."""

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        """Return up to `item.k` genomes. Returning fewer is normal; the driver turns the
        shortfall into `Failure`s so the method's batch accounting stays correct."""
        ...


@runtime_checkable
class Evaluator(Protocol):
    """Measures a genome. One per kind: an idea is not evaluated the way code is."""

    kind: str

    async def measure(
        self, ctx: EvolveContext, ind: Individual, fidelity: str = "full"
    ) -> Measurement:
        ...


class BaseMethod:
    """Defaults for the parts most methods do not need to think about."""

    name = "base"

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        return None

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        return None

    def done(self, ctx: EvolveContext) -> bool:
        return False

    def state_dict(self) -> Dict[str, Any]:
        return {}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        return None

    def reconcile(self, ctx: EvolveContext) -> None:
        return None

    def default_variator(self, **kw):
        return None

    def default_evaluators(self, **kw) -> Dict[str, Any]:
        return {}

    def context_for(self, ctx: EvolveContext, item: Create) -> PromptContext:
        return item.context
