# Circle packing — best result

**Matched the AlphaEvolve record for N=26.**

| | value |
|---|---|
| **sum of radii** | **2.6360** |
| AlphaEvolve record (N=26) | 2.635 |
| `combined_score` (= sum_radii / 2.635) | **1.0004** |
| validity | 1.0 (valid — inside the square, no overlaps) |
| min / max radius | 0.0692 / 0.1370 |
| seed baseline (naive rings) | 1.8045 |

Reproduced independently: re-running `results/packing_best.py` through
`../evaluator.py` gives `sum_radii=2.6360, validity=1.0, combined_score=1.0004`.

## How it was produced

- **Framework:** Pantheon Evolution, `single_agent_mutation=True` — one full-capability
  coding agent (a one-agent PantheonTeam) that edits a workspace, runs code, self-verifies
  with `run_evaluator`, and commits its result + a summary via a `submit()` tool.
- **Model:** `openrouter/z-ai/glm-5.2` (via OpenRouter).
- **Run:** `--iterations 3 --workers 1`. The record was found at **iteration 2** (~410 s).
- **What the agent did:** it used `run_python_code` to run a real numerical optimizer —
  joint **SLSQP** over all 26 centers + radii, with multiple random restarts and a local
  "perturb-and-reoptimize" polishing loop — verified the result with `run_evaluator`, then
  submitted. The solution (`packing_best.py`) hardcodes the optimized centers and reconstructs
  the packing deterministically.

## Reproduce

```bash
export OPENROUTER_API_KEY=...   # from ~/.env
python examples/evolution_circle_packing/run_evolution.py \
    --iterations 3 --workers 1 --model openrouter/z-ai/glm-5.2 --output results/
```

> **OpenRouter note:** use `--workers 1`. At `--workers 2` the concurrent streaming load
> caused persistent `APIConnectionError` / `ReadTimeout` and the run made no progress;
> at `--workers 1` it was flawless.

## Files

- `packing_best.py` — the evolved solution (sum of radii 2.6360).
- `evolution_report.json` — the run's report.
