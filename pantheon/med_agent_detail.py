# How PantheonOS Agent Flow Actually Works
# ==========================================
# Based on reading the actual source code.
#
# THE CORE LOOP (what happens inside agent.run())
# ================================================
#
#   1. User sends a task (string)
#   2. Agent prepends its `instructions` as system prompt
#   3. LLM receives: [system_prompt + task]
#   4. LLM decides: "I need to call a tool" → returns tool_calls
#   5. PantheonOS executes the tool (your Python function)
#   6. Tool result is appended to history
#   7. LLM receives: [system_prompt + task + tool_result]
#   8. LLM decides again: call another tool, OR stop and answer
#   9. Loop until LLM stops calling tools → returns final text
#
# THE KEY INSIGHT
# ===============
# PantheonOS is NOT a pipeline where YOU call agents in sequence.
# The LLM decides what to do. You give it tools. It figures out the order.
# Your job is:
#   1. Write clear tools (Python functions with good docstrings)
#   2. Write clear instructions (system prompt)
#   3. Choose the right team pattern
#
# TEAM PATTERNS
# =============
#
#   AgentAsToolTeam  ← best for your case
#   ┌─────────────────────────────────────────────────────┐
#   │  RouterAgent (leader)                               │
#   │  has 2 auto-generated tools:                        │
#   │   - list_sub_agents()    → see what agents exist    │
#   │   - call_sub_agent(name, instruction) → delegate    │
#   └─────────────────────────────────────────────────────┘
#         │ calls
#         ▼
#   SegmentationAgent   EvalAgent
#   (each is a full     (each has its
#    Agent with its      own tools and
#    own tools)          instructions)
#
#   SequentialTeam  ← agents run in fixed order, pass output forward
#   SwarmTeam       ← multiple agents work on same task in parallel
#   MoATeam         ← mixture of agents, aggregate their responses


import asyncio
from pantheon.agent import Agent
from pantheon.team import AgentAsToolTeam
import subprocess
import json
import sys
from pathlib import Path

import os
os.environ["LLM_API_BASE"] = "http://localhost:11434/v1"
os.environ["LLM_API_KEY"] = "ollama"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: TOOLS
# Plain Python functions. Docstring = what the LLM sees.
# Type hints = what parameters the LLM can pass.
# Return value = what the LLM reads to decide next step.
# ─────────────────────────────────────────────────────────────────────────────

SKILLS = Path(__file__).parent / "skills_scripts"
LOCAL_MODEL = "qwen2.5:72b-instruct-q4_K_M"


def run_segmentation(
    image_path: str,
    output_dir: str,
    task: str = "total",
    roi_subset: str = "liver spleen kidney_right kidney_left",
    fast: bool = False,
    statistics: bool = True,
) -> dict:
    """
    Segment anatomical structures from a CT or MRI image using TotalSegmentator.

    Args:
        image_path:  Path to input NIfTI (.nii.gz) or DICOM folder
        output_dir:  Directory to save segmentation masks
        task:        'total' for CT (117 structures) | 'total_mr' for MRI
        roi_subset:  Space-separated organs to segment e.g. 'liver kidney_right'
                     Use this to limit segmentation to only needed organs (faster)
        fast:        True = 3mm model (faster), False = full resolution (accurate)
        statistics:  True = also compute volume (ml) and mean intensity per organ

    Returns:
        dict: {status, segmentation_files, structures_found, statistics}
    """
    script = SKILLS / "organ-segmentation" / "run_segmentation.py"
    cmd = [sys.executable, str(script),
           "--input", image_path, "--output", output_dir,
           "--task", task, "--gpu", "1"]   # GPU 1 for segmentation models
    if roi_subset:
        cmd += ["--roi_subset"] + roi_subset.split()
    if fast:
        cmd.append("--fast")
    if statistics:
        cmd.append("--statistics")

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        return {"status": "error", "error": r.stderr[:500]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "error": f"Bad output: {r.stdout[:200]}"}


def evaluate_segmentation(
    pred_dir: str,
    gt_dir: str,
    output_path: str = None,
) -> dict:
    """
    Compute Dice score and HD95 for segmentation masks vs ground truth.
    Call this after run_segmentation when ground truth masks are available.

    Args:
        pred_dir:    Directory with predicted masks from run_segmentation
        gt_dir:      Directory with ground truth masks (.nii.gz, same filenames)
        output_path: Where to save eval.json (auto-set if not provided)

    Returns:
        dict: {status, mean_dice, mean_hd95, organs: {organ: {dice, hd95_mm}}}
              mean_dice is the primary quality metric: 1.0 = perfect, 0.0 = wrong
    """
    if output_path is None:
        output_path = str(Path(pred_dir).parent / "eval.json")

    script = SKILLS / "organ-segmentation" / "eval_segmentation.py"
    cmd = [sys.executable, str(script),
           "--pred_dir", pred_dir, "--gt_dir", gt_dir, "--output", output_path]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return {"status": "error", "error": r.stderr[:500]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "error": f"Bad output: {r.stdout[:200]}"}


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: AGENTS
# Each Agent gets:
#   - name:         identity
#   - instructions: system prompt — what it does and how it behaves
#   - model:        which LLM to use
#   - tools:        list of Python functions it can call
#
# The LLM reads the instructions + tool docstrings to decide what to call.
# ─────────────────────────────────────────────────────────────────────────────

segmentation_agent = Agent(
    name="SegmentationAgent",
    icon="🫁",
    description="Segments anatomical organs from CT/MRI images using TotalSegmentator.",
    instructions="""
You are a medical image segmentation specialist.
When given an image path and task, use run_segmentation to segment the organs.

Guidelines:
- CT images → task='total'
- MRI images → task='total_mr'  
- Always set statistics=True to get organ volumes
- Use roi_subset to limit to only the requested organs (much faster)
- Use fast=True for quick checks, fast=False for final results
- Report the volume (ml) of each segmented organ from the statistics
""",
    model=LOCAL_MODEL,
    tools=[run_segmentation],
    tool_timeout=1800,
)

eval_agent = Agent(
    name="EvalAgent",
    icon="📊",
    description="Evaluates segmentation quality: Dice score and HD95 vs ground truth.",
    instructions="""
You are a medical image evaluation specialist.
When given predicted masks and ground truth, use evaluate_segmentation to score them.

Guidelines:
- Always report mean_dice as the primary metric
- Dice > 0.9  = excellent
- Dice 0.7-0.9 = acceptable  
- Dice < 0.7  = poor — flag these organs specifically
- Also report HD95 in mm for each organ
- Compare predicted vs GT volumes if statistics are available
""",
    model=LOCAL_MODEL,
    tools=[evaluate_segmentation],
)

# RouterAgent has NO tools of its own.
# AgentAsToolTeam automatically gives it:
#   - list_sub_agents()              → see available agents
#   - call_sub_agent(name, task)     → delegate to a sub-agent
router_agent = Agent(
    name="RouterAgent",
    icon="🧭",
    description="Orchestrates organ segmentation and evaluation tasks.",
    instructions="""
You are a medical imaging orchestrator.
You coordinate segmentation and evaluation by delegating to specialist agents.

Available sub-agents (use call_sub_agent to delegate):
- SegmentationAgent: runs TotalSegmentator on CT/MRI images
- EvalAgent:         computes Dice + HD95 vs ground truth masks

Workflow:
1. Always start by calling SegmentationAgent with the image path
2. If ground truth is provided, call EvalAgent with pred_dir and gt_dir
3. Summarize: organs segmented, volumes in ml, Dice scores if available

Extract from the task:
- image_path: the CT/MRI file
- output_dir: where to save results
- gt_dir:     ground truth directory (optional — only run eval if provided)
- organs:     which organs (default: liver spleen kidney_right kidney_left)
""",
    model=LOCAL_MODEL,
    tools=[],   # RouterAgent gets list_sub_agents + call_sub_agent from the team
)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: TEAM
# AgentAsToolTeam wires everything together.
# It gives RouterAgent two tools automatically:
#   list_sub_agents()               → returns names + descriptions of sub-agents
#   call_sub_agent(name, task_str)  → runs that sub-agent, returns its response
#
# So the full LLM loop for RouterAgent looks like:
#   LLM: "I need to segment. Let me call SegmentationAgent"
#   → call_sub_agent("SegmentationAgent", "segment liver from /data/ct.nii.gz...")
#   → SegmentationAgent runs its own LLM loop:
#       LLM: "I should call run_segmentation"
#       → run_segmentation(image_path=..., roi_subset=...) executes your script
#       → LLM reads the result, formats response
#   → RouterAgent gets SegmentationAgent's response as a string
#   LLM: "Segmentation done. Now evaluate since gt_dir was provided"
#   → call_sub_agent("EvalAgent", "evaluate pred_dir=... gt_dir=...")
#   → EvalAgent runs its own loop, calls evaluate_segmentation, returns scores
#   → RouterAgent reads all results, writes final summary
#   LLM: done → returns final answer
# ─────────────────────────────────────────────────────────────────────────────

team = AgentAsToolTeam(
    leader_agent=router_agent,
    sub_agents=[segmentation_agent, eval_agent],
)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: RUN
# team.run(task) → calls router_agent.run(task)
# Everything else is driven by the LLM loop above.
# ─────────────────────────────────────────────────────────────────────────────

# async def main():
#     task = """
#     Analyze this CT scan:
#     - image_path: /data/patient_001/ct.nii.gz
#     - output_dir: /data/patient_001/seg_output
#     - organs: liver, spleen, kidney_right, kidney_left
#     - ground truth available at: /data/patient_001/gt
#     """

#     print("Running medical agent team...")
#     result = await team.run(task)
#     print("\n=== RESULT ===")
#     print(result.content)

async def main():
    task = """
    Analyze this CT scan:
    - image_path: /data/patient_001/ct.nii.gz
    - output_dir: /data/patient_001/seg_output
    - organs: liver, spleen, kidney_right, kidney_left
    - ground truth available at: /data/patient_001/gt
    """
    result = await team.run(task)
    print("=== RESULT ===")
    print(result.content)

    # If it went to background, keep polling until done
    if "bg_" in result.content:
        print("\n[Segmentation running in background, polling every 30s...]")
        while True:
            await asyncio.sleep(30)
            check = await team.run(
                "Check the status of all background tasks and report results. "
                "If segmentation is done, also run evaluation if gt_dir was provided."
            )
            print(check.content)
            # Stop when no longer pending
            if "pending" not in check.content.lower() and "running" not in check.content.lower():
                break


if __name__ == "__main__":
    asyncio.run(main())


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: EVOLVE
# Pantheon-Evolve operates at a completely different level.
# It does NOT evolve the agent or its instructions.
# It evolves the TOOL CODE (run_segmentation.py).
#
# How it works:
#   --initial   = run_segmentation.py    ← the code to mutate
#   --evaluator = evaluator.py           ← your fitness function
#
#   Each iteration:
#     1. LLM mutates run_segmentation.py (changes preprocess logic etc.)
#     2. Copies mutated file to a temp workspace
#     3. Calls evaluate(workspace_path) from evaluator.py
#     4. evaluate() runs mutated script on your eval cases → returns mean Dice
#     5. Keeps mutations with higher Dice, discards worse ones
#
# The agents and team above are NOT involved in evolution.
# Evolution happens below the agent layer — on the raw scripts.
#
# Run it:
#   python -m pantheon.evolution run \
#     --initial  .pantheon/skills/organ-segmentation/scripts/run_segmentation.py \
#     --evaluator evolve/evaluator.py \
#     --objective "Maximize Dice score for organ segmentation on CT images" \
#     --iterations 50 \
#     --output   ./evolve/results
#
# After evolution:
#   Best script → evolve/results/best/run_segmentation.py
#   Replace your original with the evolved version.
#   Then the agents above automatically use the better script.
# ─────────────────────────────────────────────────────────────────────────────