"""Convert an FSDP sharded checkpoint (DCP) back into a standalone PEFT
adapter directory that vLLM / PeftModel can load.

Why needed: `accelerate launch --config fsdp_config.yaml` with
`fsdp_state_dict_type: SHARDED_STATE_DICT` writes the LoRA weights as
`__0_0.distcp` + `__1_0.distcp` + `.metadata` under
`checkpoint-N/pytorch_model_fsdp_0/`, which vLLM cannot read. This
script:

  1. Instantiates the base Qwen3-8B + a LoRA adapter with the same
     shape as training (r=32, same target_modules).
  2. Uses torch.distributed.checkpoint.load() to pull the sharded LoRA
     weights into the model.
  3. Calls peft's `save_pretrained()` to dump a clean
     `adapter_model.safetensors` + `adapter_config.json`.

Usage:
    python rl_training/sft/export_adapter.py \
        --checkpoint-dir /home/erwinpi/scratch/sft_checkpoints/qwen3-8b-pantheon-v1/checkpoint-6 \
        --base-model Qwen/Qwen3-8B \
        --output-dir /home/erwinpi/scratch/sft_checkpoints/qwen3-8b-pantheon-v1-exported \
        --lora-r 32 --lora-alpha 64
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint-dir", required=True, type=Path,
                    help="checkpoint-N dir containing pytorch_model_fsdp_0/")
    ap.add_argument("--base-model", default="Qwen/Qwen3-8B")
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--lora-r", type=int, default=32)
    ap.add_argument("--lora-alpha", type=int, default=64)
    ap.add_argument("--lora-dropout", type=float, default=0.05)
    args = ap.parse_args()

    import torch
    import torch.distributed.checkpoint as dcp
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    dcp_dir = args.checkpoint_dir / "pytorch_model_fsdp_0"
    if not dcp_dir.exists():
        raise SystemExit(f"DCP dir missing: {dcp_dir}")

    print(f"[export] base_model    = {args.base_model}")
    print(f"[export] checkpoint    = {dcp_dir}")
    print(f"[export] output_dir    = {args.output_dir}")

    tok = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
        device_map="cpu",
    )

    lora_cfg = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora_cfg)

    import pickle
    with open(dcp_dir / ".metadata", "rb") as f:
        md = pickle.load(f)
    # DCP keys: "model.base_model.model...lora_A.weight"
    # PEFT keys (with FSDP use_orig_params): "base_model.model...lora_A.default.weight"
    dcp_keys = [k for k in md.state_dict_metadata if k.startswith("model.")]
    print(f"[export] DCP contains {len(dcp_keys)} keys (all LoRA)")

    def dcp_to_peft(k: str) -> str:
        k = k[len("model."):]  # strip outer "model." wrapper from PeftModel.model
        k = k.replace(".lora_A.weight", ".lora_A.default.weight")
        k = k.replace(".lora_B.weight", ".lora_B.default.weight")
        return k

    full = model.state_dict()
    # Build a shell state_dict keyed by the DCP key-names, values = the
    # corresponding PEFT tensors (which dcp.load will overwrite in-place)
    shell = {}
    for dk in dcp_keys:
        peft_key = dcp_to_peft(dk)
        if peft_key not in full:
            raise SystemExit(f"PEFT key not found for DCP {dk!r} → {peft_key!r}")
        shell[dk] = full[peft_key].clone()
    print(f"[export] matched {len(shell)} LoRA tensors; loading DCP…")

    dcp.load(state_dict=shell, checkpoint_id=str(dcp_dir))

    # Now copy into the model under the PEFT key names
    renamed = {dcp_to_peft(k): v for k, v in shell.items()}
    missing_keys, unexpected = model.load_state_dict(renamed, strict=False)
    print(f"[export] load_state_dict: unexpected={len(unexpected)} "
          f"missing={len([m for m in missing_keys if 'lora' in m])} lora-missing")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.output_dir))
    tok.save_pretrained(str(args.output_dir))
    print(f"[export] wrote PEFT adapter to {args.output_dir}")


if __name__ == "__main__":
    main()
