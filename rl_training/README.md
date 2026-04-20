# RL Training — Pantheon Evolve → Qwen

Pipeline for training a specialized Qwen3-8B to replace the frozen Claude/GPT calls in Pantheon Evolve, starting with batch correction (Harmony).

See `proposal_v8.tex` for the full research proposal and `notes.md` for the running design log.

## Status (2026-04-20)

- Branch: `rl-training` (forked from `gps-clean`).
- Trajectory logger wired into `EvolutionTeam`. Enabled via `EvolutionConfig.sft_trajectory_path`.
- CS224R reference materials in `references/cs224r/` (gitignored).
- 310-iter Harmony run from March has score history only — `save_all_programs=false` in that run meant no prompts/diffs were persisted. A re-run with logging on is required to build the SFT corpus.

## Trajectory logger

Module: `pantheon/evolution/trajectory_logger.py`. Instantiated in `EvolutionTeam.__init__` when `config.sft_trajectory_path` is set, and called once per iteration right before `IterationResult` is returned.

### JSONL schema (one record per iteration)

| Field | Source | Notes |
|---|---|---|
| `schema_version` | constant (=1) | bump when the shape changes |
| `timestamp` | wall clock | seconds epoch |
| `task_id` | caller-supplied | optional, pass via `extra=` for now |
| `iteration`, `generation`, `parent_id`, `child_id` | evolution loop | |
| `analyzer_prompt` | `Program.analysis_prompt_used` | full prompt sent to analyzer |
| `analyzer_output` | `Program.analysis_used` | analyzer's final text (`response.content`) |
| `analyzer_messages_raw` | **NEW** `Program.analyzer_messages_raw` | full `response.details.messages` list: system+user+assistant turns incl. `reasoning_content` and tool-call/tool-result pairs (think tool, python interpreter) |
| `mutator_prompt` | `Program.mutator_prompt_used` | full prompt sent to mutator |
| `mutator_response_raw` | **NEW** `Program.mutator_response_raw` | full raw mutator text response incl. any reasoning around the SEARCH/REPLACE blocks |
| `mutator_messages_raw` | **NEW** `Program.mutator_messages_raw` | full mutator `response.details.messages` list |
| `diff` | `Program.diff_from_parent` | normalized diff recomputed from snapshots |
| `mutation_summary`, `mutation_category`, `is_algorithmic` | summarizer output | |
| `metrics` | `Program.metrics` | iLISI/cLISI/mixing/bio_conservation/speed/... |
| `parent_score`, `child_score`, `improvement`, `accepted` | evolution loop | `accepted` = MAP-Elites archive decision |
| `error` | child.error if any | |
| `mutation_time`, `evaluation_time`, `llm_cost` | timers | |

### What's captured

All of the following, per iteration:

- **Analyzer**: prompt, final text, AND the full `response.details.messages` list (this preserves `reasoning_content` blocks for models that surface them — Qwen3 `<think>` mode, Claude extended thinking — plus any `think`-tool or python-interpreter tool calls and their results).
- **Mutator**: prompt, raw text response, AND the full `response.details.messages` list.
- **Diff**: normalized diff recomputed from snapshots (clean `<answer>` block).
- **Scores & metrics**: per-metric dict + composite fitness + improvement + accepted flag — reward signal for GRPO.
- **Lineage**: parent/child IDs, generation, mutation_summary/category — enough for ThetaEvolve-style dynamic bank + lazy-penalty deduplication.

This is enough for Approach B SFT (`output = <think>{reasoning}</think>\n{diff}`) AND for richer training regimes that imitate explicit chain-of-thought (reasoning_content comes through in `analyzer_messages_raw`).

## How to enable

```python
from pantheon.evolution import EvolutionTeam, EvolutionConfig

config = EvolutionConfig(
    # ... existing Harmony config ...
    save_prompts=True,            # required for the logger to have content
    save_all_programs=True,       # keep full Program JSONs on disk too
    sft_trajectory_path="rl_training/trajectories/harmony_run1.jsonl",
)
team = EvolutionTeam(config=config)
result = await team.evolve(initial_code=..., evaluator_code=..., objective=...)
```

The logger appends to the file (safe across the 8 parallel workers via an internal lock, fsync on every write). `rl_training/trajectories/` is gitignored.

## Next steps (in order)

1. **Kick off a Harmony re-run** with `sft_trajectory_path` set. Same config as the March run (`examples/evolution_batch_correction/evolution_harmonypy/results/config.yaml`). ~10h, ~$30.
2. **Write `rl_training/build_sft_dataset.py`** — consumes the JSONL, emits `(input, output, reward)` pairs in the Approach B format: `output = f"<think>{analyzer_output}</think>\n{mutator_response_raw}"`.
3. **Baseline `use_analyzer=false` run** to validate Option 1 (merged analyzer+mutator) before committing to it.
4. **SFT on Qwen3-8B** with HF TRL `SFTTrainer` — see proposal_v8 §6.
5. **GRPO on Qwen3-8B** — proposal_v8 §6.3. Reward wraps `child.fitness_score(...)` with optional lazy penalty.
6. **bio-evolve integration** (later): add `benchmarks/harmony-batchcorr-pantheon/` to the bio-evolve repo, pointing at the trained model's vLLM endpoint.

## References

- `references/cs224r/` — Stanford CS224R default project guidelines (PDF + starter zip). Structural reference only; the default project is arithmetic, not genomics.
- `proposal_v8.tex` — research proposal.
- `notes.md` — running design log incl. analyzer-mutator coupling analysis.
