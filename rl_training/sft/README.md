# Pantheon SFT pipeline — shared LoRA on analyzer + mutator traces

Offline imitation learning on Pantheon-Evolve trajectories. One LoRA
adapter over Qwen3-8B, trained on the mixed stream of
`(analyzer_prompt → analyzer_output)` and `(mutator_prompt → mutator_response_raw)`
pairs extracted from `trajectory.jsonl`. At inference, Pantheon still
makes two calls per iteration; both hit the same adapter.

## 1. Prepare data from a Sonnet corpus run

```bash
python rl_training/sft/prepare_data.py \
    --trajectory /home/erwinpi/bio-evolve/results/harmony-tma-pantheon/<ts>/trajectory.jsonl \
    --out-dir rl_training/sft/data/pantheon_harmony_v1
```

`N` iterations → `2·N` training pairs (minus any where the analyzer or
mutator returned empty). Pass `--drop-errors` to skip records flagged
with an evaluation error.

## 2. Train the LoRA

```bash
source /home/erwinpi/bio-evolve/.env.ml     # HF_HOME, WANDB_PROJECT
python rl_training/sft/train.py \
    --data-jsonl rl_training/sft/data/pantheon_harmony_v1/train.jsonl \
    --base-model Qwen/Qwen3-8B \
    --output-dir /home/erwinpi/scratch/sft_checkpoints/qwen3-8b-pantheon-v1 \
    --wandb-run-name qwen3-8b-pantheon-v1
```

Defaults: rank 32, alpha 64, 3 epochs, effective batch 16, bf16,
completion-only loss (only the assistant turn contributes to gradient),
cosine LR 5e-5 with 5% warmup. 60 pairs of 4k tokens fits in one
A100-40GB; ~20 min wall.

## 3. Serve with vLLM (Phase 4 baseline)

```bash
vllm serve Qwen/Qwen3-8B \
    --enable-lora \
    --lora-modules pantheon-v1=/home/erwinpi/scratch/sft_checkpoints/qwen3-8b-pantheon-v1 \
    --max-lora-rank 32 \
    --port 8000
```

Then point Pantheon's `analyzer_model` and `mutator_model` at
`pantheon-v1` via the local OpenAI-compatible endpoint.
