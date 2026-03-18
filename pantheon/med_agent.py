"""
Organ Segmentation — Complete Agent Flow
=========================================

Architecture:
    RouterAgent (leader)
        ├── calls → SegmentationAgent  (runs TotalSegmentator)
        └── calls → EvalAgent          (computes Dice + HD95)

Team pattern: AgentAsToolTeam
    - RouterAgent is the leader
    - SegmentationAgent and EvalAgent are sub-agents (called as tools)
    - Router decides WHAT to run and WHEN based on the task

Usage:
    python agent_flow.py --image ct.nii.gz --gt_dir ./gt --output_dir ./out
    python agent_flow.py --image ct.nii.gz --output_dir ./out   # no eval
"""

import argparse
import asyncio
from email import parser
import json
import subprocess
import sys
from pathlib import Path

from pantheon.agent import Agent
from pantheon.team import AgentAsToolTeam
import argparse

import os
os.environ["LLM_API_BASE"] = "http://localhost:11434/v1"
os.environ["LLM_API_KEY"] = "ollama"

# ── Paths ─────────────────────────────────────────────────────────────────────

SKILLS_DIR  = Path(__file__).parent / "skills_scripts"
RUN_SCRIPT  = SKILLS_DIR / "organ-segmentation" / "run_segmentation.py"
EVAL_SCRIPT = SKILLS_DIR / "eval-segmentation"  / "eval_segmentation.py"

# Local Ollama model — change to your pulled model name
LOCAL_MODEL = "qwen2.5:72b-instruct-q4_K_M"  # or "ollama/llama3.3:70b-instruct-q4_K_M"



# ── Tools (plain Python functions — Agent.tool() wraps these) ─────────────────

def run_segmentation(
    image_path: str,
    output_dir: str,
    task: str = "total",
    roi_subset: str = "liver spleen kidney_right kidney_left",
    fast: bool = False,
    statistics: bool = True,
    gpu: int = 0,
) -> dict:
    """
    Run TotalSegmentator on a CT or MRI image to segment anatomical structures.

    Args:
        image_path: Path to input NIfTI (.nii.gz) or DICOM folder
        output_dir: Directory to save segmentation masks
        task:       'total' (CT, 117 structures) | 'total_mr' (MRI) | 'lung_vessels'
        roi_subset: Space-separated organ names to segment. Faster than full segmentation.
                    e.g. 'liver spleen kidney_right kidney_left'
        fast:       Use 3mm resolution model (faster, slightly less accurate)
        statistics: Compute volume (mm³) and mean intensity per structure
        gpu:        GPU index to use (0, 1, or 2)

    Returns:
        dict with keys: status, segmentation_files, structures_found,
                        statistics_file, statistics (if requested), error
    """
    cmd = [
        sys.executable, str(RUN_SCRIPT),
        "--input",     image_path,
        "--output",    output_dir,
        "--task",      task,
        "--gpu",       str(gpu),
    ]
    if roi_subset:
        cmd += ["--roi_subset"] + roi_subset.split()
    if fast:
        cmd.append("--fast")
    if statistics:
        cmd.append("--statistics")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

    if result.returncode != 0:
        return {
            "status": "error",
            "error":  result.stderr[:1000] or "run_segmentation failed",
        }

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error":  f"Could not parse output: {result.stdout[:500]}",
        }


def evaluate_segmentation(
    pred_dir: str,
    gt_dir: str,
    output_path: str = None,
) -> dict:
    """
    Evaluate segmentation quality by computing Dice score and HD95 against
    ground truth masks. Call this after run_segmentation.

    Args:
        pred_dir:    Directory containing predicted masks from run_segmentation
        gt_dir:      Directory containing ground truth masks (.nii.gz)
        output_path: Where to save eval.json (optional)

    Returns:
        dict with keys: status, mean_dice, mean_hd95, organs (per-organ scores)
        mean_dice is the primary quality metric (0.0 = worst, 1.0 = perfect)
    """
    if output_path is None:
        output_path = str(Path(pred_dir).parent / "eval.json")

    cmd = [
        sys.executable, str(EVAL_SCRIPT),
        "--pred_dir", pred_dir,
        "--gt_dir",   gt_dir,
        "--output",   output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        return {
            "status": "error",
            "error":  result.stderr[:1000] or "eval_segmentation failed",
        }

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error":  f"Could not parse eval output: {result.stdout[:500]}",
        }


# ── Agents ────────────────────────────────────────────────────────────────────

def build_segmentation_agent() -> Agent:
    agent = Agent(
        name="SegmentationAgent",
        icon="🫁",
        description="Runs TotalSegmentator to segment organs from CT/MRI images.",
        instructions="""
You are a medical image segmentation specialist.
You use the run_segmentation tool to segment anatomical structures from CT or MRI images.

Rules:
- Always use roi_subset to limit segmentation to only the organs needed
- Use task='total' for CT, task='total_mr' for MRI
- Use fast=True during testing/evolution, fast=False for final results
- Always enable statistics=True to get volume measurements
- Return the full result dict including structures_found and statistics
""",
        model=LOCAL_MODEL,
        tools=[run_segmentation],
        tool_timeout=1800,
    )
    return agent


def build_eval_agent() -> Agent:
    agent = Agent(
        name="EvalAgent",
        icon="📊",
        description="Evaluates segmentation quality: Dice score and HD95 vs ground truth.",
        instructions="""
You are a medical image evaluation specialist.
You use the evaluate_segmentation tool to compute Dice scores and HD95 distances.

Rules:
- Always call evaluate_segmentation after segmentation is complete
- Report mean_dice as the primary quality metric
- Flag any organ with Dice < 0.7 as poor quality
- Report volume differences between pred and GT if > 20%
""",
        model=LOCAL_MODEL,
        tools=[evaluate_segmentation],
    )
    return agent

def build_debug_agent() -> Agent:
    agent = Agent(
        name="DebugAgent",
        icon="🐞",
        description="Helps debug errors in segmentation or evaluation.",
        instructions="You are a medical imaging debugging assistant. You analyze error messages and logs to identify issues in the segmentation or evaluation process. You suggest specific code changes or parameter adjustments to fix the problem.",
        model=LOCAL_MODEL,
        tools=[],
    )
    return agent



def build_router_agent() -> Agent:
    agent = Agent(
    name="RouterAgent",
    icon="🧭",
    description="Orchestrates organ segmentation and evaluation tasks.",
    instructions="""
You are a medical imaging pipeline. Execute tasks autonomously without asking for confirmation.

RULES:
- NEVER ask the user questions. NEVER say "would you like to proceed".
- NEVER ask for confirmation before running tools.
- If segmentation fails, retry ONCE with fast=True, then report the error.
- Always call SegmentationAgent immediately with the given parameters.
- If ground truth is provided, always call EvalAgent after segmentation.
- Report results directly. Do not suggest next steps or ask follow-up questions.

CRITICAL: call_sub_agent instruction must be a plain text string, NOT a dict.

Workflow (execute all steps without stopping):
1. Call SegmentationAgent immediately with the image path and parameters given
2. If gt_dir was provided, call EvalAgent with pred_dir and gt_dir
3. Print final summary with organs, volumes, and Dice scores
4. If there's an error at any step, call DebugAgent with the error message to analyze and suggest fixes
""",
    model=LOCAL_MODEL,
    tools=[],)
    
    return agent


# ── Team ──────────────────────────────────────────────────────────────────────

def build_team() -> AgentAsToolTeam:
    router      = build_router_agent()
    segmentator = build_segmentation_agent()
    evaluator   = build_eval_agent()
    debugger     = build_debug_agent()

    team = AgentAsToolTeam(
        leader_agent=router,
        sub_agents=[segmentator, evaluator, debugger],
    )
    return team


# ── Evolution evaluator (used by Pantheon-Evolve) ────────────────────────────

def evaluate(workspace_path: str) -> float:
    """
    Pantheon-Evolve fitness function.

    This function is called by Pantheon-Evolve after each code mutation.
    It runs the (possibly mutated) run_segmentation.py on your eval set
    and returns mean Dice as the fitness score.

    Args:
        workspace_path: Path to the mutated code workspace (managed by Evolve)

    Returns:
        float: mean Dice score (0.0 worst → 1.0 perfect)
    """
    import os

    # Eval dataset — update these paths
    EVAL_CASES_DIR = Path(os.environ.get("EVAL_CASES_DIR", "./data/eval"))
    GT_BASE_DIR    = Path(os.environ.get("GT_BASE_DIR",    "./data/gt"))

    if not EVAL_CASES_DIR.exists():
        print(f"[evaluate] Eval cases dir not found: {EVAL_CASES_DIR}")
        return 0.0

    total_dice = []

    for case_dir in sorted(EVAL_CASES_DIR.iterdir()):
        if not case_dir.is_dir():
            continue

        image_path = case_dir / "image.nii.gz"
        gt_dir     = GT_BASE_DIR / case_dir.name
        output_dir = Path(workspace_path) / "outputs" / case_dir.name

        if not image_path.exists() or not gt_dir.exists():
            continue

        # Use the (possibly mutated) run_segmentation.py from workspace
        mutated_run_script = Path(workspace_path) / "run_segmentation.py"
        if not mutated_run_script.exists():
            mutated_run_script = RUN_SCRIPT  # fallback to original

        # Run segmentation
        seg_cmd = [
            sys.executable, str(mutated_run_script),
            "--input",     str(image_path),
            "--output",    str(output_dir),
            "--task",      "total",
            "--fast",                       # fast=True for speed during evolution
            "--roi_subset", "liver", "spleen", "kidney_right", "kidney_left",
            "--gpu",       "0",
        ]
        seg_result = subprocess.run(seg_cmd, capture_output=True, text=True, timeout=600)
        if seg_result.returncode != 0:
            total_dice.append(0.0)
            continue

        # Evaluate
        eval_cmd = [
            sys.executable, str(EVAL_SCRIPT),
            "--pred_dir", str(output_dir),
            "--gt_dir",   str(gt_dir),
            "--output",   str(output_dir / "eval.json"),
        ]
        eval_result = subprocess.run(eval_cmd, capture_output=True, text=True, timeout=120)
        if eval_result.returncode != 0:
            total_dice.append(0.0)
            continue

        try:
            eval_data = json.loads(eval_result.stdout)
            total_dice.append(float(eval_data.get("mean_dice", 0.0)))
        except (json.JSONDecodeError, ValueError):
            total_dice.append(0.0)

    if not total_dice:
        return 0.0

    mean_dice = sum(total_dice) / len(total_dice)
    print(f"[evaluate] Cases={len(total_dice)}  Mean Dice={mean_dice:.4f}")
    return mean_dice


# ── CLI ───────────────────────────────────────────────────────────────────────

async def run_agent(image_path: str, output_dir: str, gt_dir: str = None):
    team = build_team()

    task = f"""
            Analyze this medical image:
            - image_path: {image_path}
            - output_dir: {output_dir}
            - organs to segment: liver, spleen, kidney_right, kidney_left
            - modality: CT
            """
    if gt_dir:
        task += f"- gt_dir: {gt_dir} (run evaluation after segmentation)\n"
    else:
        task += "- No ground truth available, skip evaluation\n"

    result = await team.run(task)
    print("\n" + "="*60)
    print("FINAL RESULT:")
    print("="*60)
    print(result.content)

def parse_args():
    parser = argparse.ArgumentParser(description="Organ Segmentation Agent")
    parser.add_argument("--image",      help="Input CT/MRI NIfTI path", default="datasets/data_exp_ct-mr/CT_case00002/CT_Case_00002_0000.nii.gz")
    parser.add_argument("--output_dir", help="Output directory", default="tmp/test_agent_output")
    parser.add_argument("--gt_dir",     help="Ground truth dir (optional)", default="datasets/data_exp_ct-mr/CT_case00002/gt")
    return parser.parse_args()

def main():
    args = parse_args()
    # parser = argparse.ArgumentParser(description="Organ Segmentation Agent")
    # parser.add_argument("--image",      required=True, help="Input CT/MRI NIfTI path", default="datasets/data_exp_ct-mr/CT_case00002/CT_Case_00002_0000.nii.gz")
    # parser.add_argument("--output_dir", required=True, help="Output directory", default="tmp/test_agent_output")
    # parser.add_argument("--gt_dir",     help="Ground truth dir (optional)", default="datasets/data_exp_ct-mr/CT_case00002/gt")
    # args = parser.parse_args()

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    asyncio.run(run_agent(args.image, args.output_dir, args.gt_dir))


if __name__ == "__main__":
    main()
