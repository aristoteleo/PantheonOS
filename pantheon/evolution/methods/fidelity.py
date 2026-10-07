"""An online controller for a two-level fidelity cascade: how many candidates to screen at the cheap
fidelity, and which of them deserve a full measurement.

A fixed cascade (screen k, promote the best p) assumes the cheap reading is as trustworthy and as
cheap everywhere. It is not: on the dr task of the PantheonEvo bench, a 1,000-cell reading ranks
programs like the full one with Spearman ~0.8 overall but recovers only a third of the true top
10%, and a program whose cost is a fixed number of iterations is barely cheaper on fewer cells. So
the controller learns, from the run itself:

  * the CALIBRATION: every candidate measured at both fidelities is a (cheap, full) pair; from the
    pairs, a least-squares line full ~ a + b * cheap, the residual spread sigma, and Spearman's rho;
  * the COSTS: the evaluation time of each fidelity (the evaluator's own seconds when it reports
    them, the measurement's duration otherwise).

and decides, per inner step:

  * screen width: base_k + (cost ratio - 1) * rho * screen_gain, within [k_min, k_max] -- wider
    the cheaper and the more trustworthy the cheap reading; and no screening at all (a plain full
    step) once rho has fallen below rho_off on enough pairs;
  * promotions: a candidate is promoted when its optimistic full prediction (a + b * cheap +
    z * sigma) reaches the incumbent (the trajectory's best full score); always the best one by
    the cheap reading so the step makes progress, at most promote_max.

Only promoted candidates get a full reading, so pairs from promotions alone describe the top of
the cheap scale and nothing else. With probability `explore` a step also promotes one candidate
drawn at random from the rest, which keeps the calibration honest over the whole range.

Until `min_pairs` pairs exist the controller runs a conservative fixed cascade (warm_k, warm_promote).
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _spearman(x: Sequence[float], y: Sequence[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for t in range(i, j + 1):
                r[order[t]] = (i + j) / 2.0
            i = j + 1
        return r
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


class FidelityController:
    def __init__(self, cheap: str = "low", base_k: int = 2, k_min: int = 2, k_max: int = 8,
                 promote_max: int = 2, z: float = 1.0, explore: float = 0.2, min_pairs: int = 8,
                 rho_off: float = 0.5, warm_k: int = 4, warm_promote: int = 2,
                 default_cost_ratio: float = 3.0, screen_gain: float = 1.5, seed: int = 0):
        self.cheap = cheap
        self.base_k, self.k_min, self.k_max = int(base_k), int(k_min), int(k_max)
        self.promote_max, self.z, self.explore = int(promote_max), float(z), float(explore)
        self.min_pairs, self.rho_off = int(min_pairs), float(rho_off)
        self.warm_k, self.warm_promote = int(warm_k), int(warm_promote)
        self.default_cost_ratio = float(default_cost_ratio)
        self.screen_gain = float(screen_gain)
        self.rng = random.Random(seed)
        self.pairs: List[Tuple[float, float]] = []
        self.costs: Dict[str, List[float]] = {}

    # ---- observations -------------------------------------------------------------

    def observe_pair(self, cheap: float, full: float) -> None:
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (cheap, full)):
            self.pairs.append((float(cheap), float(full)))

    def observe_cost(self, fidelity: str, secs: Optional[float]) -> None:
        if isinstance(secs, (int, float)) and secs > 0 and math.isfinite(secs):
            self.costs.setdefault(fidelity, []).append(float(secs))

    # ---- what has been learned -------------------------------------------------------

    def cost(self, fidelity: str) -> Optional[float]:
        v = self.costs.get(fidelity) or []
        if not v:
            return None
        v = sorted(v[-50:])
        return v[len(v) // 2]                 # median of the recent ones: robust to a slow node

    def cost_ratio(self) -> float:
        cf, cc = self.cost("full"), self.cost(self.cheap)
        if cf and cc:
            return max(1.0, cf / cc)
        return self.default_cost_ratio

    def calibration(self) -> Dict[str, Any]:
        n = len(self.pairs)
        out: Dict[str, Any] = {"n": n, "cost_ratio": round(self.cost_ratio(), 3)}
        if n < 3:
            return out
        xs, ys = [p[0] for p in self.pairs], [p[1] for p in self.pairs]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in self.pairs) / sxx if sxx > 0 else 0.0
        a = my - b * mx
        res = [y - (a + b * x) for x, y in self.pairs]
        sigma = math.sqrt(sum(r * r for r in res) / max(1, n - 2))
        out.update({"a": a, "b": b, "sigma": sigma, "rho": _spearman(xs, ys)})
        return out

    def warm(self) -> bool:
        return len(self.pairs) < self.min_pairs

    def screening(self) -> bool:
        """False once enough pairs show the cheap reading does not rank like the full one."""
        if self.warm():
            return True
        return self.calibration().get("rho", 0.0) >= self.rho_off

    # ---- decisions ---------------------------------------------------------------------

    def k_screen(self) -> Optional[int]:
        """Cheap readings for this step, or None for a plain full step of base_k."""
        if self.warm():
            return max(self.k_min, self.warm_k)
        cal = self.calibration()
        rho = cal.get("rho", 0.0)
        if rho < self.rho_off:
            return None
        # screen wider the cheaper and the more trustworthy the cheap reading is: at cost ratio r
        # (full / cheap) and rank agreement rho, k = base_k + (r - 1) * rho * gain. A step then
        # costs more clock than a plain one when r is modest; what it buys is a better candidate
        # per full reading, which is the scarce thing on a slow evaluator.
        r = self.cost_ratio()
        k = int(round(self.base_k + (r - 1.0) * max(0.0, min(1.0, rho)) * self.screen_gain))
        return max(self.k_min, min(self.k_max, k))

    def promote(self, cands: Sequence[Tuple[str, float]], incumbent: Optional[float]) -> Dict[str, Any]:
        """`cands`: (id, cheap score) of the step's feasible candidates. Returns the ids to measure
        in full and why."""
        ranked = sorted(cands, key=lambda c: c[1], reverse=True)
        if not ranked:
            return {"promote": [], "explored": None, "rule": "none feasible"}
        if self.warm():
            chosen = [c[0] for c in ranked[: max(1, self.warm_promote)]]
            rule = "warm-up"
        else:
            cal = self.calibration()
            a, b, s = cal.get("a", 0.0), cal.get("b", 1.0), cal.get("sigma", 0.0)
            chosen = [ranked[0][0]]                       # the step always makes progress
            for cid, x in ranked[1:]:
                if len(chosen) >= self.promote_max:
                    break
                if incumbent is not None and a + b * x + self.z * s >= incumbent:
                    chosen.append(cid)
            rule = "ucb"
        explored = None
        rest = [c[0] for c in ranked if c[0] not in chosen]
        if rest and self.rng.random() < self.explore:
            explored = self.rng.choice(rest)
            chosen.append(explored)
        return {"promote": chosen, "explored": explored, "rule": rule}

    # ---- persistence --------------------------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {"pairs": list(self.pairs), "costs": {k: list(v[-200:]) for k, v in self.costs.items()}}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.pairs = [tuple(p) for p in state.get("pairs", [])]
        self.costs = {k: list(v) for k, v in (state.get("costs") or {}).items()}
