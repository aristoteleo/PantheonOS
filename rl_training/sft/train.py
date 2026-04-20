"""LoRA SFT of Qwen3-8B on Pantheon analyzer+mutator trajectories.

Shared single LoRA adapter trained on mixed-role data — the same weights
learn both roles. Works off the `{"messages": [...]}` JSONL produced by
`prepare_data.py`.

Design
------
- Base model  : Qwen3-8B (local snapshot in HF_HOME)
- Adapter     : LoRA rank 32, alpha 64, dropout 0.05
                target = q/k/v/o + gate/up/down projections
- Precision   : bf16 + gradient checkpointing
- Loss        : completion-only — tokens before the final assistant turn
                are masked so gradient only flows through the target
                response (Qwen3 chat template marker `<|im_start|>assistant\\n`)
- Optimizer   : AdamW, lr=5e-5, cosine schedule, 5% warmup
- Epochs      : 3  (enough on <100 examples with LoRA regularization)
- Batching    : per-device bs=1, grad-accum=16 → effective 16
                (keeps 4096-token sequences in one A100-40GB)
- Telemetry   : wandb (run name includes timestamp + adapter tag)

Usage
-----
    python rl_training/sft/train.py \
        --data-jsonl rl_training/sft/data/pantheon_harmony_v1/train.jsonl \
        --base-model Qwen/Qwen3-8B \
        --output-dir /home/erwinpi/scratch/sft_checkpoints/qwen3-8b-pantheon-v1 \
        --wandb-run-name qwen3-8b-pantheon-v1

The resulting adapter can be loaded by vLLM via `--enable-lora` and
`--lora-modules <name>=<path>`, or merged into the base weights with
`peft.PeftModel.merge_and_unload()`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-jsonl", required=True, type=Path,
                    help="JSONL with {\"messages\": [...]} per line")
    ap.add_argument("--base-model", default="Qwen/Qwen3-8B",
                    help="HF model id or local snapshot path")
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--wandb-run-name", default=None)
    ap.add_argument("--max-seq-length", type=int, default=16384,
                    help="Token cap — examples over this are dropped, not truncated "
                         "(observed Pantheon/Harmony pairs: p50≈9.3k, max≈12.4k tokens)")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--per-device-batch-size", type=int, default=1)
    ap.add_argument("--grad-accum-steps", type=int, default=16)
    ap.add_argument("--lora-r", type=int, default=32)
    ap.add_argument("--lora-alpha", type=int, default=64)
    ap.add_argument("--lora-dropout", type=float, default=0.05)
    ap.add_argument("--eval-split", type=float, default=0.1,
                    help="Fraction of data held out for eval (0 disables)")
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


def _filter_by_length(dataset, tokenizer, max_len: int):
    def _len(example):
        text = tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False
        )
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]
        return {"_len": len(ids)}

    dataset = dataset.map(_len, num_proc=1)
    lens = dataset["_len"]
    print(f"[train] token-length stats: min={min(lens)} p50={sorted(lens)[len(lens)//2]} "
          f"max={max(lens)} (cap={max_len})")
    before = len(dataset)
    dataset = dataset.filter(lambda e: e["_len"] <= max_len)
    after = len(dataset)
    if before != after:
        print(f"[train] length filter: {before} → {after} (dropped {before - after} over {max_len} toks)")
    return dataset.remove_columns(["_len"])


def main() -> None:
    args = _parse_args()

    # Defer heavy imports so --help is fast
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    os.environ.setdefault("WANDB_PROJECT", "pantheon-evolve-qwen-sft")
    if args.wandb_run_name:
        os.environ["WANDB_NAME"] = args.wandb_run_name

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[train] base_model   = {args.base_model}")
    print(f"[train] data_jsonl   = {args.data_jsonl}")
    print(f"[train] output_dir   = {args.output_dir}")
    print(f"[train] max_seq_len  = {args.max_seq_length}")
    print(f"[train] epochs       = {args.epochs}")
    print(f"[train] lora r/alpha = {args.lora_r}/{args.lora_alpha}")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    raw = load_dataset("json", data_files=str(args.data_jsonl), split="train")
    raw = _filter_by_length(raw, tokenizer, args.max_seq_length)

    if args.eval_split > 0 and len(raw) >= 10:
        split = raw.train_test_split(test_size=args.eval_split, seed=args.seed)
        train_ds, eval_ds = split["train"], split["test"]
    else:
        train_ds, eval_ds = raw, None
    print(f"[train] train={len(train_ds)}  eval={len(eval_ds) if eval_ds else 0}")

    lora_cfg = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    sft_cfg = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.per_device_batch_size,
        per_device_eval_batch_size=args.per_device_batch_size,
        gradient_accumulation_steps=args.grad_accum_steps,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        optim="adamw_torch",
        max_grad_norm=1.0,
        bf16=True,
        tf32=True,
        logging_steps=1,
        save_strategy="epoch",
        save_total_limit=2,
        eval_strategy="epoch" if eval_ds is not None else "no",
        report_to=["wandb"],
        seed=args.seed,
        max_length=args.max_seq_length,
        packing=False,
        completion_only_loss=True,
        assistant_only_loss=True,
        dataset_kwargs={"skip_prepare_dataset": False},
    )

    trainer = SFTTrainer(
        model=args.base_model,
        args=sft_cfg,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        peft_config=lora_cfg,
    )

    train_out = trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))

    summary = {
        "train_metrics": {k: float(v) for k, v in train_out.metrics.items()
                          if isinstance(v, (int, float))},
        "n_train": len(train_ds),
        "n_eval": len(eval_ds) if eval_ds else 0,
        "base_model": args.base_model,
        "lora": {"r": args.lora_r, "alpha": args.lora_alpha,
                 "dropout": args.lora_dropout},
    }
    (args.output_dir / "train_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n[train] saved adapter to {args.output_dir}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
