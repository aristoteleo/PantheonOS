"""A judge that predicts the verifier instead of grading prose.

The judge this replaces scored an approach from 0 to 1 on how promising it looked. Measured on
Erdos minimum-overlap, every score it produced landed inside [0.80, 0.92] while the objective it
was supposed to anticipate ranged over [0.50, 0.62]. The two numbers were never comparable, so a
method that sorted them together always put an unjudged idea above a measured one, and the judge
selected nothing. That is not a prompt problem and no amount of instructing the model to "be
harsher" fixes it.

So this judge is asked a question that has a right answer: **given the score you are starting
from, what score will implementing this approach reach?** The prediction is then a gain on the
verifier's own scale, directly comparable with a measured gain, and -- because it is a prediction
of an observable -- it can be scored, calibrated, and reported as right or wrong.

Two layers, and they fix different things:

  `_surrogate`    the model, shown up to `n_examples` approaches that were actually implemented
                  and what each actually gained. This is what improves the *ranking*.
  `Calibration`   an isotonic (monotone, non-parametric) regression from the model's raw number
                  onto the measured gain. This is what fixes the *scale*, and it is immune to the
                  range compression above: whatever narrow band the model insists on using gets
                  stretched onto the range the objective actually occupies.

`sigma` is the calibration's residual spread, not a confidence interval the model states about
itself. A model asked how sure it is will answer fluently and wrongly; the residuals are the only
honest estimate available, and they are what the method's exploration bonus is scaled by.

Below `n_min` observations the calibration is the identity and `sigma` is a wide prior. With five
labelled ideas an isotonic fit is a way of drawing a curve through noise, and pretending otherwise
would put a confident number in front of the method at exactly the moment it has no information.

**`state_dict` matters more here than anywhere else in the package.** One run labels on the order
of ten ideas. Learning anything from ten points is not possible, so the training set has to
outlive the run that produced it -- see `save`/`load`.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pantheon.utils.log import logger

from ..core.individual import Individual
from ..core.method import EvolveContext
from ..core.work import Measurement

JUDGE_SYSTEM = (
    "You are predicting the outcome of a proposed approach to an optimisation problem, before it "
    "is implemented. You are given the objective, the score the current best program achieves, "
    "and the approach. Some approaches that were already implemented are shown with the score "
    "they actually reached; use them to calibrate.\n\n"
    "Predict the score a competent implementation of this approach will reach. Predicting the "
    "current score means you expect it to change nothing; predicting less means you expect it to "
    "make things worse, which is a legitimate answer. Reply with JSON only: "
    '{"score": <number>, "reason": "<one sentence>"}'
)


def pava(xs: Sequence[float], ys: Sequence[float]) -> Tuple[List[float], List[float]]:
    """Pool adjacent violators: the least-squares monotone non-decreasing fit to `ys` ordered by
    `xs`. Returns `(sorted_xs, fitted_ys)`, which `numpy.interp` turns into a function.

    Monotone rather than linear because the failure being corrected is a *compressed* range, not a
    shifted one. A linear fit can rescale [0.80, 0.92] onto the right interval only if the model's
    numbers are linearly related to outcomes; isotonic asks only that they be ordered, which is a
    far weaker claim and the only one the evidence supports.
    """
    if not xs:
        return [], []
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    sx = [float(xs[i]) for i in order]
    v = [float(ys[i]) for i in order]
    w = [1.0] * len(v)
    i = 0
    while i < len(v) - 1:
        if v[i] <= v[i + 1] + 1e-12:
            i += 1
            continue
        tot = w[i] + w[i + 1]
        v[i] = (v[i] * w[i] + v[i + 1] * w[i + 1]) / tot
        w[i] = tot
        del v[i + 1], w[i + 1]
        if i > 0:
            i -= 1
    fitted: List[float] = []
    for val, wt in zip(v, w):
        fitted.extend([val] * int(round(wt)))
    return sx, fitted[: len(sx)]


def _interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> float:
    """Linear interpolation, clamped at both ends. Stands in for `numpy.interp` so this module
    stays importable without numpy."""
    if not xs:
        return x
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def _spearman(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n < 3:
        return float("nan")
    ra = _ranks(a)
    rb = _ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return float(num / (da * db)) if da > 0 and db > 0 else float("nan")


def _ranks(v: Sequence[float]) -> List[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


class Calibration:
    """Maps a raw prediction onto the scale of measured gains, and reports how well it does.

    Held at the identity until `n_min` observations exist. The alternative -- fitting whatever is
    available -- produces its most confident distortions exactly when the data cannot support one.
    """

    def __init__(self, n_min: int = 5, prior_sigma: float = 0.15):
        self.n_min = n_min
        self.prior_sigma = prior_sigma
        self.xs: List[float] = []
        self.ys: List[float] = []
        self._kx: List[float] = []
        self._ky: List[float] = []
        self.sigma = prior_sigma
        self.rho = float("nan")
        self.slope = float("nan")
        self.fitted = False

    def __len__(self) -> int:
        return len(self.xs)

    def observe(self, raw: float, gain: float) -> None:
        self.xs.append(float(raw))
        self.ys.append(float(gain))

    def fit(self) -> Dict[str, Any]:
        if len(self.xs) < self.n_min:
            self.fitted = False
            self.sigma = self.prior_sigma
            self._kx, self._ky = [], []
            return self.report()
        self._kx, self._ky = pava(self.xs, self.ys)
        self.fitted = True
        resid = [y - _interp(x, self._kx, self._ky) for x, y in zip(self.xs, self.ys)]
        n = len(resid)
        mean = sum(resid) / n
        var = sum((r - mean) ** 2 for r in resid) / max(1, n - 1)
        # A monotone fit through n points has residuals that understate out-of-sample error; the
        # floor keeps sigma from collapsing to zero and switching exploration off by accident.
        self.sigma = max(0.02, math.sqrt(var))
        self.rho = _spearman(self.xs, self.ys)
        sx, sy = self.xs, self.ys
        mx, my = sum(sx) / n, sum(sy) / n
        den = sum((x - mx) ** 2 for x in sx)
        self.slope = (sum((x - mx) * (y - my) for x, y in zip(sx, sy)) / den) if den > 0 else float("nan")
        return self.report()

    def __call__(self, raw: float) -> float:
        if not self.fitted:
            return float(raw)
        return float(_interp(float(raw), self._kx, self._ky))

    def report(self) -> Dict[str, Any]:
        return {"n": len(self.xs), "fitted": self.fitted, "sigma": round(self.sigma, 5),
                "spearman": (None if math.isnan(self.rho) else round(self.rho, 4)),
                "slope": (None if math.isnan(self.slope) else round(self.slope, 4))}

    def state_dict(self) -> Dict[str, Any]:
        return {"xs": list(self.xs), "ys": list(self.ys),
                "n_min": self.n_min, "prior_sigma": self.prior_sigma}

    def load_state_dict(self, s: Dict[str, Any]) -> None:
        self.xs = [float(v) for v in s.get("xs", [])]
        self.ys = [float(v) for v in s.get("ys", [])]
        self.n_min = int(s.get("n_min", self.n_min))
        self.prior_sigma = float(s.get("prior_sigma", self.prior_sigma))
        self.fit()


class LearnedIdeaJudge:
    """Predicts what an approach will score, and learns from what it did score.

    Registered as the evaluator for `kind="idea"`, but owned by the method: the method decides when
    `fit` runs, because refit timing is a scheduling decision and the loop has no opinion about it.
    """

    kind = "idea"

    def __init__(
        self,
        *,
        model: str = "high",
        objective: str = "",
        n_min: int = 5,
        prior_sigma: float = 0.15,
        n_examples: int = 8,
        timeout: float = 120,
        system_prompt: Optional[str] = None,
        base_fn: Optional[Callable[[EvolveContext, Individual], float]] = None,
    ):
        self.model = model
        self.objective = objective
        self.n_examples = n_examples
        self.timeout = timeout
        self.system_prompt = system_prompt or JUDGE_SYSTEM
        self.cal = Calibration(n_min=n_min, prior_sigma=prior_sigma)
        self.base_fn = base_fn
        """How to find the score an idea would start from. Installed by the method, which is the
        only component that knows how inheritance works."""
        self.examples: List[Tuple[str, float, float]] = []   # (text, base, measured gain)

    # ---- learning --------------------------------------------------------

    def observe(self, text: str, raw: float, base: float, gain: float) -> None:
        self.cal.observe(raw, gain)
        self.examples.append((text, float(base), float(gain)))

    def fit(self) -> Dict[str, Any]:
        return self.cal.fit()

    @property
    def sigma(self) -> float:
        return self.cal.sigma

    def report(self) -> Dict[str, Any]:
        return self.cal.report()

    # ---- prediction ------------------------------------------------------

    def _exemplars(self) -> str:
        if not self.examples:
            return ""
        # Widest-spread first: exemplars that all gained the same amount teach the model nothing
        # about ordering, which is the thing it is being asked for.
        picked = sorted(self.examples, key=lambda e: -abs(e[2]))[: self.n_examples]
        picked = sorted(picked, key=lambda e: e[2])
        rows = []
        for text, base, gain in picked:
            head = " ".join(text.split())[:260]
            rows.append(f"- started at {base:.4f}, reached {base + gain:.4f} "
                        f"(gain {gain:+.4f}): {head}")
        return ("\n## Approaches already implemented, and what they actually reached\n"
                + "\n".join(rows))

    def build_prompt(self, ctx: EvolveContext, ind: Individual, base: float) -> str:
        return (f"## Objective\n{self.objective or ctx.objective}\n\n"
                f"## The score to beat\nThe program this approach would start from scores "
                f"{base:.4f}.\n"
                f"{self._exemplars()}\n\n"
                f"## The proposed approach\n{ind.genome.render()}\n\n"
                f"Predict the score a competent implementation will reach.")

    async def measure(self, ctx: EvolveContext, ind: Individual,
                      fidelity: str = "full") -> Measurement:
        from openai import AsyncOpenAI

        from pantheon.utils.llm_providers import detect_provider

        t0 = time.time()
        base = 0.0
        if self.base_fn is not None:
            try:
                base = float(self.base_fn(ctx, ind))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"judge base_fn failed: {type(e).__name__}: {e}")

        cfg = detect_provider(self.model, False)
        client = AsyncOpenAI(api_key=cfg.api_key or os.environ.get("OPENAI_API_KEY"),
                             base_url=cfg.base_url or os.environ.get("OPENAI_API_BASE") or None,
                             timeout=self.timeout)
        try:
            resp = await client.chat.completions.create(
                model=cfg.model_name,
                messages=[{"role": "system", "content": self.system_prompt},
                          {"role": "user", "content": self.build_prompt(ctx, ind, base)}],
            )
            text = (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"idea judge failed: {type(e).__name__}: {e}")
            # A judge that could not answer must not veto the idea. Predict no change and hand it
            # the widest uncertainty, so the exploration bonus -- not the failure -- decides.
            return Measurement(
                individual_id=ind.id, fidelity=fidelity, ok=True,
                metrics={"idea_base": base, "idea_raw": 0.0, "idea_delta_hat": 0.0,
                         "idea_sigma": max(self.cal.sigma, self.cal.prior_sigma)},
                artifacts={"error": str(e)[:200]}, duration=time.time() - t0)

        pred, reason = _parse_prediction(text, base)
        raw = pred - base
        dh = self.cal(raw)
        return Measurement(
            individual_id=ind.id, fidelity=fidelity, ok=True,
            metrics={"idea_base": base, "idea_pred_score": pred, "idea_raw": raw,
                     "idea_delta_hat": dh, "idea_sigma": self.cal.sigma},
            artifacts={"judgement": reason, "raw": text[:400], "calibrated": self.cal.fitted,
                       "n_train": len(self.cal)},
            duration=time.time() - t0,
        )

    # ---- persistence -----------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {"calibration": self.cal.state_dict(),
                "examples": [[t, b, g] for t, b, g in self.examples]}

    def load_state_dict(self, s: Dict[str, Any]) -> None:
        self.cal.load_state_dict(s.get("calibration", {}))
        self.examples = [(str(t), float(b), float(g)) for t, b, g in s.get("examples", [])]

    def save(self, path: str) -> None:
        """Carry the training set to the next run.

        A run labels roughly one idea per implemented approach -- ten, on a normal budget. Fitting
        anything to ten points is self-deception, so a judge that cannot outlive its run cannot
        learn at all. This is the mechanism that makes the word "learned" true.
        """
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state_dict(), fh, indent=1)
        os.replace(tmp, path)

    def load(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as fh:
            self.load_state_dict(json.load(fh))
        logger.info(f"judge loaded {len(self.cal)} prior observations from {path}")
        return True


def _parse_prediction(text: str, base: float) -> Tuple[float, str]:
    """Pull a predicted score out of the reply.

    Falls back to `base` -- "no change" -- rather than to zero. A parse failure is the judge saying
    nothing, and scoring it as a catastrophic prediction would put a made-up number into the
    training set and corrupt the calibration it is meant to feed.
    """
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            return float(d["score"]), str(d.get("reason", ""))[:300]
        except Exception:  # noqa: BLE001
            pass
    m = re.search(r"-?\d+\.\d+", text or "")
    if m:
        try:
            return float(m.group(0)), (text or "")[:300]
        except ValueError:
            pass
    return float(base), (text or "")[:300]
