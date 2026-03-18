"""
Flow:
  user task
    └── RouterAgent
          ├── run_segmentation()    ← subprocess → run_segmentation.py → TotalSegmentator
          ├── evaluate_segmentation() ← subprocess → eval_segmentation.py → Dice/HD95
          └── final summary

Usage:
    python med_agent.py
    python med_agent.py --image ct.nii.gz --output_dir ./out
    python med_agent.py --image ct.nii.gz --output_dir ./out --gt_dir ./gt
    python med_agent.py --image mri.nii.gz --output_dir ./out --task total_mr --fast
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

# ── Must be set BEFORE any pantheon import ────────────────────────────────────
os.environ.setdefault("LLM_API_BASE", "http://localhost:11434/v1")
os.environ.setdefault("LLM_API_KEY",  "ollama")

from pantheon.agent import Agent  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

LOCAL_MODEL  = "qwen2.5:72b-instruct-q4_K_M"

# Script paths — update these to match your actual layout
# Your server has them at .pantheon/skills/*/scripts/
BASE_DIR     = Path(__file__).parent.parent
RUN_SCRIPT   = BASE_DIR / "skills_scripts" / "organ-segmentation" / "run_segmentation.py"
EVAL_SCRIPT  = BASE_DIR / "skills_scripts" / "organ-segmentation" / "eval_segmentation.py"
# If the above don't exist, fall back to skills_scripts/ layout:
if not RUN_SCRIPT.exists():
    RUN_SCRIPT  = BASE_DIR / "skills_scripts" / "organ-segmentation" / "run_segmentation.py"
if not EVAL_SCRIPT.exists():
    EVAL_SCRIPT = BASE_DIR / "skills_scripts" / "organ-segmentation" / "eval_segmentation.py"

# ── Tools ─────────────────────────────────────────────────────────────────────
# Plain Python functions. The docstring is what the LLM reads to decide when
# and how to call each tool. Return type must be str (not dict) — PantheonOS
# writes the return value to disk and to the conversation history.

def run_segmentation(
    image_path: str,
    output_dir: str,
    task: str = "total",
    roi_subset: str = "liver spleen kidney_right kidney_left",
    fast: bool = False,
    statistics: bool = True,
    gpu: int = 1,
) -> str:
    """
    Segment anatomical structures from a CT or MRI image using TotalSegmentator.

    Args:
        image_path:  Absolute path to input NIfTI (.nii.gz) or DICOM folder
        output_dir:  Directory to save segmentation mask files
        task:        'total' for CT (117 structures) | 'total_mr' for MRI (50 structures)
        roi_subset:  Space-separated organ names to segment, e.g. 'liver spleen kidney_right'
                     Always set this to limit to only the needed organs — much faster
        fast:        True = 3mm model (2-5 min) | False = full resolution (10-20 min)
        statistics:  True = also compute volume (ml) and mean HU per organ
        gpu:         GPU index for TotalSegmentator inference (use 1 or 2, not 0)

    Returns:
        JSON string with: status, structures_found, output_dir, statistics (volumes),
        segmentation_files, error (if failed)
    """
    cmd = [
        sys.executable, str(RUN_SCRIPT),
        "--input",  image_path,
        "--output", output_dir,
        "--task",   task,
        "--gpu",    str(gpu),
    ]
    if roi_subset:
        cmd += ["--roi_subset"] + roi_subset.split()
    if fast:
        cmd.append("--fast")
    if statistics:
        cmd.append("--statistics")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "error": "Segmentation timed out after 3600s"})
    except FileNotFoundError:
        return json.dumps({"status": "error", "error": f"Script not found: {RUN_SCRIPT}"})

    if proc.returncode != 0:
        return json.dumps({
            "status": "error",
            "error":  proc.stderr[:1000] or "run_segmentation.py failed",
            "returncode": proc.returncode,
        })

    # run_segmentation.py prints JSON to stdout
    try:
        result = json.loads(proc.stdout)
        return json.dumps(result)
    except json.JSONDecodeError:
        return json.dumps({
            "status": "error",
            "error":  f"Could not parse output: {proc.stdout[:500]}",
        })


def evaluate_segmentation(
    pred_dir: str,
    gt_dir: str,
    output_path: str = "",
) -> str:
    """
    Compute Dice score and HD95 for segmentation results vs ground truth masks.
    Call this after run_segmentation when ground truth is available.

    Args:
        pred_dir:    Directory with predicted masks (.nii.gz) from run_segmentation
        gt_dir:      Directory with ground truth masks (.nii.gz) — same filenames
        output_path: Where to save eval.json (leave empty to auto-set)

    Returns:
        JSON string with: status, mean_dice, mean_hd95, organs (per-organ dice+hd95),
        error (if failed).
        mean_dice interpretation: >0.9 excellent | 0.7-0.9 acceptable | <0.7 poor
    """
    if not output_path:
        output_path = str(Path(pred_dir).parent / "eval.json")

    cmd = [
        sys.executable, str(EVAL_SCRIPT),
        "--pred_dir", pred_dir,
        "--gt_dir",   gt_dir,
        "--output",   output_path,
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "error": "Evaluation timed out after 600s"})
    except FileNotFoundError:
        return json.dumps({"status": "error", "error": f"Script not found: {EVAL_SCRIPT}"})

    if proc.returncode != 0:
        return json.dumps({
            "status": "error",
            "error":  proc.stderr[:1000] or "eval_segmentation.py failed",
        })

    try:
        result = json.loads(proc.stdout)
        return json.dumps(result)
    except json.JSONDecodeError:
        return json.dumps({
            "status": "error",
            "error":  f"Could not parse eval output: {proc.stdout[:500]}",
        })


# ── Agent ─────────────────────────────────────────────────────────────────────

def build_agent() -> Agent:
    return Agent(
        name="CTImagingAgent",
        icon="🏥",
        instructions="""
You are a CT imaging pipeline. Execute tasks autonomously and immediately.

STRICT RULES — follow these without exception:
- Call run_segmentation IMMEDIATELY with the parameters given. Do not ask questions.
- If gt_dir is provided, call evaluate_segmentation after segmentation completes.
- If gt_dir is NOT provided or is empty, skip evaluation entirely.
- If segmentation fails, retry ONCE with fast=True. If it fails again, report the error.
- NEVER ask the user for confirmation. NEVER say "would you like to proceed".
- NEVER suggest alternatives or ask follow-up questions.
- Report results directly and concisely when done.

TOOL CALL RULES:
- roi_subset must be a space-separated string: "liver spleen kidney_right kidney_left"
- All paths must be strings, not dicts
- tools return JSON strings — parse them to read status, structures_found, statistics

OUTPUT FORMAT (after all tools complete):
  Segmentation: <N> structures | Output: <output_dir>
  Volumes: liver=<X>ml, spleen=<X>ml, ...
  Evaluation (if run): mean_dice=<X> | <organ>=<dice>, ...
  Flags: list any organ with dice < 0.7
""",
        model=LOCAL_MODEL,
        tools=[run_segmentation, evaluate_segmentation],
        tool_timeout=1800,   # 30 min — segmentation can be slow on full resolution
    )


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_agent(
    image_path: str,
    output_dir: str,
    task: str = "total",
    roi_subset: list[str] = None,
    fast: bool = False,
    gt_dir: str = None,
) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    roi = " ".join(roi_subset) if roi_subset else "liver spleen kidney_right kidney_left"

    task_str = f"""EXECUTE immediately without asking questions:

1. Run segmentation:
   image_path: {image_path}
   output_dir: {output_dir}
   task: {task}
   roi_subset: {roi}
   fast: {fast}
   statistics: True
   gpu: 1

2. {"Run evaluation with gt_dir: " + gt_dir if gt_dir else "Skip evaluation — no gt_dir provided."}

3. Report results.
"""

    agent = build_agent()

    print(f"\n{'='*60}")
    print(f"Image:  {image_path}")
    print(f"Output: {output_dir}")
    print(f"Task:   {task}  |  Fast: {fast}  |  Organs: {roi}")
    if gt_dir:
        print(f"GT:     {gt_dir}")
    print(f"{'='*60}\n")

    result = await agent.run(task_str)

    print(f"\n{'='*60}")
    print("RESULT:")
    print(f"{'='*60}")
    print(result.content)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CT imaging agent — segmentation + evaluation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--image",
        default="datasets/data_exp_ct-mr/CT_case00002/CT_Case_00002_0000.nii.gz",
        help="Input CT/MRI NIfTI path",
    )
    parser.add_argument(
        "--output_dir",
        default="tmp/test_agent_output",
        help="Output directory for segmentation masks",
    )
    parser.add_argument(
        "--task",
        default="total",
        choices=["total", "total_mr", "lung_vessels"],
        help="Segmentation task: total=CT, total_mr=MRI",
    )
    parser.add_argument(
        "--roi_subset",
        nargs="+",
        default=["liver", "spleen", "kidney_right", "kidney_left"],
        help="Organs to segment",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use 3mm fast model",
    )
    parser.add_argument(
        "--gt_dir",
        default=None,
        help="Ground truth directory for evaluation (optional)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run_agent(
        image_path=args.image,
        output_dir=args.output_dir,
        task=args.task,
        roi_subset=args.roi_subset,
        fast=args.fast,
        gt_dir=args.gt_dir,
    ))


if __name__ == "__main__":
    main()