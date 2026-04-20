"""Build an SFT corpus from one or more Pantheon `trajectory.jsonl` files.

Each evolution iteration produces two supervised examples:

1. **Analyzer pair** — the exact chat that the analyzer model saw
   (`analyzer_messages_raw`) with the assistant turn set to
   `analyzer_output` (the `<think>` + critique block).

2. **Mutator pair** — the exact chat that the mutator model saw
   (`mutator_messages_raw`) with the assistant turn set to
   `mutator_response_raw` (reasoning + SEARCH/REPLACE diff).

Both pairs are interleaved into a single dataset — the same LoRA adapter
learns both roles ("shared-single-LoRA on mixed-role data"). No role tag
is injected into the user prompt: the model disambiguates the two tasks
from the prompt content itself (which already differs sharply between
analyzer and mutator calls).

Records flagged with errors in `error` / `phase` are kept only if the
assistant turn is non-empty — Pantheon sometimes records an error after
a partial generation, and the partial generation is still a useful
signal for format-learning.

Usage
-----
    python rl_training/sft/prepare_data.py \
        --trajectory /path/to/trajectory.jsonl [--trajectory /path/to/other.jsonl ...] \
        --out-dir rl_training/sft/data/pantheon_harmony_v1

Outputs
-------
    <out-dir>/train.jsonl        — one {"messages": [...]} per line
    <out-dir>/stats.json         — counts per role, token-length histogram
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _record_to_pairs(rec: dict) -> list[dict]:
    """Build SFT pairs from the rendered prompt strings (not the raw message
    chain — the analyzer chain captures only the assistant's tool-use loop and
    does not include the initial user prompt). At inference we train Qwen to
    emit the final response directly, skipping the record_thought scaffolding.
    """
    pairs: list[dict] = []

    analyzer_prompt = (rec.get("analyzer_prompt") or "").strip()
    analyzer_output = (rec.get("analyzer_output") or "").strip()
    if analyzer_prompt and analyzer_output:
        pairs.append({
            "role_hint": "analyzer",
            "messages": [
                {"role": "user", "content": analyzer_prompt},
                {"role": "assistant", "content": analyzer_output},
            ],
            "iteration": rec.get("iteration"),
            "child_id": rec.get("child_id"),
        })

    mutator_prompt = (rec.get("mutator_prompt") or "").strip()
    mutator_output = (rec.get("mutator_response_raw") or "").strip()
    if mutator_prompt and mutator_output:
        pairs.append({
            "role_hint": "mutator",
            "messages": [
                {"role": "user", "content": mutator_prompt},
                {"role": "assistant", "content": mutator_output},
            ],
            "iteration": rec.get("iteration"),
            "child_id": rec.get("child_id"),
        })
    return pairs


def _load_trajectory(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trajectory", action="append", required=True, type=Path,
                    help="Path to trajectory.jsonl (can be repeated to mix runs)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--drop-errors", action="store_true",
                    help="Skip records where the evaluation phase errored")
    args = ap.parse_args()

    all_pairs: list[dict] = []
    per_file_counts: dict[str, int] = {}
    for tp in args.trajectory:
        if not tp.exists():
            raise SystemExit(f"trajectory not found: {tp}")
        recs = _load_trajectory(tp)
        if args.drop_errors:
            recs = [r for r in recs if not r.get("error")]
        file_pairs: list[dict] = []
        for r in recs:
            file_pairs.extend(_record_to_pairs(r))
        per_file_counts[str(tp)] = len(file_pairs)
        all_pairs.extend(file_pairs)
        print(f"[prepare_data] {tp.name}: {len(recs)} records → {len(file_pairs)} pairs")

    if not all_pairs:
        raise SystemExit("no training pairs produced — check trajectory fields")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = args.out_dir / "train.jsonl"
    with out_jsonl.open("w") as fh:
        for p in all_pairs:
            fh.write(json.dumps({"messages": p["messages"]}, ensure_ascii=False) + "\n")

    role_counts = Counter(p["role_hint"] for p in all_pairs)
    char_lens = [sum(len(m["content"]) for m in p["messages"]) for p in all_pairs]
    stats = {
        "total_pairs": len(all_pairs),
        "by_role": dict(role_counts),
        "by_file": per_file_counts,
        "char_length": {
            "min": min(char_lens),
            "max": max(char_lens),
            "mean": sum(char_lens) // len(char_lens),
        },
        "out_jsonl": str(out_jsonl),
    }
    (args.out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(f"\n[prepare_data] wrote {out_jsonl}  ({len(all_pairs)} pairs)")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
