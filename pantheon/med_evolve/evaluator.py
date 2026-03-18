"""
Pantheon-Evolve Evaluator for Organ Segmentation.

Pantheon-Evolve calls evaluate(workspace_path) after each mutation.
It copies the mutated run_segmentation.py into workspace_path,
then calls this function to get a fitness score.

The fitness score = mean Dice across your eval cases.
Higher = better. Range: 0.0 (worst) to 1.0 (perfect).

Setup before running evolution:
    export EVAL_CASES_DIR=./data/eval     # folder with case_*/image.nii.gz
    export GT_BASE_DIR=./data/gt          # folder with case_*/liver.nii.gz etc.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Paths to your scripts (adjust if needed)
_HERE          = Path(__file__).parent
EVAL_SCRIPT    = _HERE / ".pantheon/skills/eval-segmentation/scripts/eval_segmentation.py"
ORIG_RUN_SCRIPT = _HERE / ".pantheon/skills/organ-segmentation/scripts/run_segmentation.py"

# Eval dataset paths — override via environment variables
EVAL_CASES_DIR = Path(os.environ.get("EVAL_CASES_DIR", str(_HERE / "data/eval")))
GT_BASE_DIR    = Path(os.environ.get("GT_BASE_DIR",    str(_HERE / "data/gt")))

# Organs to evaluate (must match your GT mask filenames)
ORGANS = ["liver", "spleen", "kidney_right", "kidney_left"]


def evaluate(workspace_path: str) -> float:
    """
    Fitness function called by Pantheon-Evolve after each mutation.

    Pantheon-Evolve will:
    1. Mutate run_segmentation.py (the preprocess/postprocess logic)
    2. Place mutated code in workspace_path/
    3. Call this function with workspace_path
    4. Use the returned float as fitness (higher = better)

    Args:
        workspace_path: Directory containing the mutated code

    Returns:
        float: Mean Dice score across all eval cases (0.0 – 1.0)
    """
    workspace = Path(workspace_path)

    # Use mutated script if it exists, else fall back to original
    run_script = workspace / "run_segmentation.py"
    if not run_script.exists():
        run_script = ORIG_RUN_SCRIPT

    if not EVAL_CASES_DIR.exists():
        print(f"[evaluator] ⚠ EVAL_CASES_DIR not found: {EVAL_CASES_DIR}", file=sys.stderr)
        print(f"[evaluator] Set: export EVAL_CASES_DIR=/path/to/your/eval/cases", file=sys.stderr)
        return 0.0

    case_dirs = sorted([d for d in EVAL_CASES_DIR.iterdir() if d.is_dir()])
    if not case_dirs:
        print(f"[evaluator] ⚠ No cases found in {EVAL_CASES_DIR}", file=sys.stderr)
        return 0.0

    dice_scores = []

    for case_dir in case_dirs:
        image_path = case_dir / "image.nii.gz"
        gt_dir     = GT_BASE_DIR / case_dir.name
        output_dir = workspace / "outputs" / case_dir.name

        # Skip if data is missing
        if not image_path.exists():
            print(f"[evaluator] ⚠ No image: {image_path}", file=sys.stderr)
            continue
        if not gt_dir.exists():
            print(f"[evaluator] ⚠ No GT dir: {gt_dir}", file=sys.stderr)
            continue

        output_dir.mkdir(parents=True, exist_ok=True)

        # ── 1. Run segmentation ───────────────────────────────────────────────
        seg_cmd = [
            sys.executable, str(run_script),
            "--input",      str(image_path),
            "--output",     str(output_dir),
            "--task",       "total",
            "--fast",                          # fast mode = quicker eval loop
            "--roi_subset", *ORGANS,
            "--gpu",        "0",
        ]

        try:
            seg = subprocess.run(
                seg_cmd,
                capture_output=True, text=True, timeout=600
            )
            if seg.returncode != 0:
                print(f"[evaluator] ✗ Segmentation failed: {case_dir.name}", file=sys.stderr)
                dice_scores.append(0.0)
                continue
        except subprocess.TimeoutExpired:
            print(f"[evaluator] ✗ Segmentation timeout: {case_dir.name}", file=sys.stderr)
            dice_scores.append(0.0)
            continue

        # ── 2. Evaluate Dice ──────────────────────────────────────────────────
        eval_output = output_dir / "eval.json"
        eval_cmd = [
            sys.executable, str(EVAL_SCRIPT),
            "--pred_dir", str(output_dir),
            "--gt_dir",   str(gt_dir),
            "--output",   str(eval_output),
        ]

        try:
            ev = subprocess.run(
                eval_cmd,
                capture_output=True, text=True, timeout=120
            )
            if ev.returncode != 0:
                print(f"[evaluator] ✗ Eval failed: {case_dir.name}", file=sys.stderr)
                dice_scores.append(0.0)
                continue

            result = json.loads(ev.stdout)
            dice   = float(result.get("mean_dice", 0.0))
            print(f"[evaluator] ✓ {case_dir.name}  Dice={dice:.4f}", file=sys.stderr)
            dice_scores.append(dice)

        except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as e:
            print(f"[evaluator] ✗ {case_dir.name}: {e}", file=sys.stderr)
            dice_scores.append(0.0)

    if not dice_scores:
        return 0.0

    mean_dice = sum(dice_scores) / len(dice_scores)
    print(f"[evaluator] ══ Mean Dice = {mean_dice:.4f}  ({len(dice_scores)} cases) ══",
          file=sys.stderr)
    return mean_dice
