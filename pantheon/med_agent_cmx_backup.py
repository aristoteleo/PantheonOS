"""
Flow:
  user task
    └── RouterAgent                              ← detects modality from filename / metadata
          ├── ct_agent_tool()                    ← CTImagingAgent wrapped as tool
          │     ├── run_segmentation()           → TotalSegmentator (task=total)
          │     └── evaluate_segmentation()      → Dice/HD95
          ├── mri_agent_tool()                   ← MRIImagingAgent wrapped as tool
          │     ├── run_segmentation()           → TotalSegmentator (task=total_mr)
          │     └── evaluate_segmentation()      → Dice/HD95
          └── xray_agent_tool()                  ← XRayImagingAgent wrapped as tool
                └── run_xray_classification()    → TorchXRayVision DenseNet121 (18 pathologies)

Usage:
    python med_agent.py
    python med_agent.py --image ct.nii.gz --output_dir ./out
    python med_agent.py --image ct.nii.gz --output_dir ./out --gt_dir ./gt
    python med_agent.py --image mri.nii.gz --output_dir ./out
    python med_agent.py --image mri.nii.gz --output_dir ./out --gt_dir ./gt --fast
    python med_agent.py --image xray.png --output_dir ./out --modality XRAY
    python med_agent.py --image chest.dcm --output_dir ./out --modality XRAY \
                        --xray_model densenet121-res224-nih --xray_threshold 0.4
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

# ── Step logger ───────────────────────────────────────────────────────────────

import time

_step_start: float = 0.0

def step(msg: str) -> None:
    """Print a timestamped pipeline step to stdout."""
    global _step_start
    now = time.time()
    elapsed = f"{now - _step_start:.1f}s" if _step_start else ""
    _step_start = now
    suffix = f"  [{elapsed}]" if elapsed else ""
    print(f"\n{msg}...{suffix}", flush=True)

def done(msg: str) -> None:
    """Print a completion line for the most recent step."""
    elapsed = f"{time.time() - _step_start:.1f}s"
    print(f"{msg}  [{elapsed}]", flush=True)

def fail(msg: str) -> None:
    print(f"{msg}", flush=True)

# ── Config ────────────────────────────────────────────────────────────────────

LOCAL_MODEL = "qwen2.5:72b-instruct-q4_K_M"

BASE_DIR     = Path(__file__).parent.parent
RUN_SCRIPT   = BASE_DIR / "skills_scripts" / "organ-segmentation"  / "run_segmentation.py"
EVAL_SCRIPT  = BASE_DIR / "skills_scripts" / "organ-segmentation"  / "eval_segmentation.py"
XRAY_SCRIPT  = BASE_DIR / "skills_scripts" / "xray-classification" / "run_xray_classification.py"
if not RUN_SCRIPT.exists():
    RUN_SCRIPT  = BASE_DIR / "skills_scripts" / "organ-segmentation" / "run_segmentation.py"
if not EVAL_SCRIPT.exists():
    EVAL_SCRIPT = BASE_DIR / "skills_scripts" / "organ-segmentation" / "eval_segmentation.py"
if not XRAY_SCRIPT.exists():
    XRAY_SCRIPT = BASE_DIR / "skills_scripts" / "xray-classification" / "run_xray_classification.py"

# ── Shared tool implementations ───────────────────────────────────────────────
# CT and MRI agents share the same two tools — the difference is in the
# default arguments (task=total vs total_mr) that each agent is instructed to pass.

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
    organs = roi_subset or "all"
    mode   = "fast (3mm)" if fast else "full resolution"
    step(f"Running segmentation  task={task}  organs=[{organs}]  mode={mode}")

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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        fail("Segmentation timed out after 3600s")
        return json.dumps({"status": "error", "error": "Segmentation timed out after 3600s"})
    except FileNotFoundError:
        fail(f"Script not found: {RUN_SCRIPT}")
        return json.dumps({"status": "error", "error": f"Script not found: {RUN_SCRIPT}"})

    if proc.returncode != 0:
        fail(f"Segmentation failed (exit {proc.returncode})")
        return json.dumps({
            "status": "error",
            "error":  proc.stderr[:1000] or "run_segmentation.py failed",
            "returncode": proc.returncode,
        })

    try:
        result = json.loads(proc.stdout)
        n = len(result.get("structures_found", []))
        done(f"Segmentation complete — {n} structures saved to {output_dir}")
        return json.dumps(result)
    except json.JSONDecodeError:
        fail("Could not parse segmentation output")
        return json.dumps({"status": "error", "error": f"Could not parse output: {proc.stdout[:500]}"})


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

    step(f"Running evaluation  pred={pred_dir}  gt={gt_dir}")

    cmd = [
        sys.executable, str(EVAL_SCRIPT),
        "--pred_dir", pred_dir,
        "--gt_dir",   gt_dir,
        "--output",   output_path,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        fail("Evaluation timed out after 600s")
        return json.dumps({"status": "error", "error": "Evaluation timed out after 600s"})
    except FileNotFoundError:
        fail(f"Script not found: {EVAL_SCRIPT}")
        return json.dumps({"status": "error", "error": f"Script not found: {EVAL_SCRIPT}"})

    if proc.returncode != 0:
        fail(f"Evaluation failed (exit {proc.returncode})")
        return json.dumps({
            "status": "error",
            "error":  proc.stderr[:1000] or "eval_segmentation.py failed",
        })

    try:
        result = json.loads(proc.stdout)
        dice = result.get("mean_dice", "n/a")
        hd95 = result.get("mean_hd95", "n/a")
        done(f"Evaluation complete — mean_dice={dice}  mean_hd95={hd95}  saved to {output_path}")
        return json.dumps(result)
    except json.JSONDecodeError:
        fail("Could not parse evaluation output")
        return json.dumps({"status": "error", "error": f"Could not parse eval output: {proc.stdout[:500]}"})


# ── X-Ray tool ────────────────────────────────────────────────────────────────

def run_xray_classification(
    image_path: str,
    output_dir: str = "",
    model: str = "densenet121-res224-all",
    threshold: float = 0.5,
    gpu: int = 1,
) -> str:
    """
    Classify pathologies in a chest X-ray using TorchXRayVision DenseNet121.
    Use ONLY for 2D chest X-rays (PNG, JPG, DICOM .dcm). NOT for CT or MRI.

    Models — use 'all' when unsure:
      densenet121-res224-all      best general purpose (trained on all datasets)
      densenet121-res224-nih      NIH ChestX-ray8
      densenet121-res224-chex     CheXpert / Stanford (edema, consolidation)
      densenet121-res224-rsna     RSNA Pneumonia Challenge
      resnet50-res512-all         higher resolution, slower, most accurate

    Detects 18 pathologies (sigmoid probability 0.0-1.0):
      Atelectasis, Consolidation, Infiltration, Pneumothorax, Edema,
      Emphysema, Fibrosis, Effusion, Pneumonia, Pleural_Thickening,
      Cardiomegaly, Nodule, Mass, Hernia, Lung Lesion, Fracture,
      Lung Opacity, Enlarged Cardiomediastinum

    Args:
        image_path:  Path to chest X-ray (PNG, JPG, or .dcm)
        output_dir:  Directory to save result JSON (optional)
        model:       Pretrained model weights
        threshold:   Score threshold for flagging a finding (default 0.5)
        gpu:         GPU index — use 1 or 2, leave 0 for LLM

    Returns:
        JSON string with: status, scores (all 18 sorted by score), findings (above
        threshold), critical_findings (Pneumothorax/Pneumonia/Edema/Consolidation),
        top_3, error (if failed)
    """
    output_path = ""
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_path = str(Path(output_dir) / "xray_classification.json")

    step(f"Running X-ray classification  model={model}  threshold={threshold}")

    cmd = [
        sys.executable, str(XRAY_SCRIPT),
        "--input",     image_path,
        "--model",     model,
        "--threshold", str(threshold),
        "--gpu",       str(gpu),
    ]
    if output_path:
        cmd += ["--output", output_path]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        fail("X-ray classification timed out after 300s")
        return json.dumps({"status": "error", "error": "X-ray classification timed out after 300s"})
    except FileNotFoundError:
        fail(f"Script not found: {XRAY_SCRIPT}")
        return json.dumps({"status": "error", "error": f"Script not found: {XRAY_SCRIPT}"})

    if proc.returncode != 0:
        fail(f"X-ray classification failed (exit {proc.returncode})")
        return json.dumps({
            "status": "error",
            "error":  proc.stderr[:1000] or "run_xray_classification.py failed",
            "returncode": proc.returncode,
        })

    try:
        result = json.loads(proc.stdout)
        findings = result.get("findings", [])
        critical = result.get("critical_findings", [])
        top3     = result.get("top_3", [])
        done(f"X-ray complete — findings={findings}  critical={critical}  top3={top3}")
        return json.dumps(result)
    except json.JSONDecodeError:
        fail("Could not parse X-ray classification output")
        return json.dumps({"status": "error", "error": f"Could not parse output: {proc.stdout[:500]}"})


# ── CT Agent ──────────────────────────────────────────────────────────────────

def build_ct_agent() -> Agent:
    step("Building CTImagingAgent")
    agent = Agent(
        name="CTImagingAgent",
        icon="🔬",
        instructions="""
You are a CT imaging pipeline. Execute tasks autonomously and immediately.

MODALITY DEFAULTS — always use these unless explicitly told otherwise:
- task: "total"  (CT model, 117 structures)
- roi_subset: "liver spleen kidney_right kidney_left aorta"
- gpu: 1

STRICT RULES:
- Call run_segmentation IMMEDIATELY with the parameters given. Do not ask questions.
- If gt_dir is provided, call evaluate_segmentation after segmentation completes.
- If gt_dir is NOT provided or is empty, skip evaluation entirely.
- If segmentation fails, retry ONCE with fast=True. If it fails again, report the error.
- NEVER ask the user for confirmation. NEVER say "would you like to proceed".
- NEVER suggest alternatives or ask follow-up questions.

TOOL CALL RULES:
- roi_subset must be a space-separated string: "liver spleen kidney_right kidney_left"
- All paths must be strings, not dicts
- Tools return JSON strings — parse them to read status, structures_found, statistics

OUTPUT FORMAT (after all tools complete):
  Modality: CT
  Segmentation: <N> structures | Output: <output_dir>
  Volumes: liver=<X>ml, spleen=<X>ml, ...
  Evaluation (if run): mean_dice=<X> | <organ>=<dice>, ...
  Flags: list any organ with dice < 0.7
""",
        model=LOCAL_MODEL,
        tools=[run_segmentation, evaluate_segmentation],
        tool_timeout=1800,
    )
    done("CTImagingAgent ready")
    return agent


# ── MRI Agent ─────────────────────────────────────────────────────────────────

def build_mri_agent() -> Agent:
    step("Building MRIImagingAgent")
    agent = Agent(
        name="MRIImagingAgent",
        icon="🧲",
        instructions="""
You are an MRI imaging pipeline. Execute tasks autonomously and immediately.

MODALITY DEFAULTS — always use these unless explicitly told otherwise:
- task: "total_mr"  (MRI model, 50 structures)
- roi_subset: "pancreas stomach liver spleen"
- gpu: 1

KEY DIFFERENCE FROM CT:
- MRI uses task="total_mr", NOT "total". Never use task="total" for MRI.
- MRI supports ~50 structures vs CT's 117. Do not request CT-only structures.
- MRI segmentation is generally slower — prefer fast=True unless precision is critical.
- MRI does NOT produce HU statistics (no Hounsfield units). Volume (ml) is still available.

STRICT RULES:
- Call run_segmentation IMMEDIATELY with the parameters given. Do not ask questions.
- If gt_dir is provided, call evaluate_segmentation after segmentation completes.
- If gt_dir is NOT provided or is empty, skip evaluation entirely.
- If segmentation fails, retry ONCE with fast=True. If it fails again, report the error.
- NEVER ask the user for confirmation. NEVER say "would you like to proceed".
- NEVER suggest alternatives or ask follow-up questions.

TOOL CALL RULES:
- roi_subset must be a space-separated string: "liver spleen kidney_right kidney_left"
- All paths must be strings, not dicts
- Tools return JSON strings — parse them to read status, structures_found, statistics

OUTPUT FORMAT (after all tools complete):
  Modality: MRI
  Segmentation: <N> structures | Output: <output_dir>
  Volumes: liver=<X>ml, spleen=<X>ml, ...
  Evaluation (if run): mean_dice=<X> | <organ>=<dice>, ...
  Flags: list any organ with dice < 0.7
""",
        model=LOCAL_MODEL,
        tools=[run_segmentation, evaluate_segmentation],
        tool_timeout=1800,
    )
    done("MRIImagingAgent ready")
    return agent


# ── X-Ray Agent ───────────────────────────────────────────────────────────────

def build_xray_agent() -> Agent:
    step("Building XRayImagingAgent")
    agent = Agent(
        name="XRayImagingAgent",
        icon="🩻",
        instructions="""
You are a chest X-ray classification pipeline. Execute tasks autonomously and immediately.

MODALITY DEFAULTS — always use these unless explicitly told otherwise:
- model: "densenet121-res224-all"
- threshold: 0.5
- gpu: 1

STRICT RULES:
- Call run_xray_classification IMMEDIATELY with the parameters given. Do not ask questions.
- NEVER ask the user for confirmation. NEVER say "would you like to proceed".
- NEVER suggest alternatives or ask follow-up questions.
- Do NOT call run_segmentation or evaluate_segmentation — those are for CT/MRI only.

TOOL CALL RULES:
- image_path and output_dir must be plain strings, not dicts
- Tool returns a JSON string — parse it to read status, findings, critical_findings, scores

OUTPUT FORMAT (after tool completes):
  Modality: X-Ray
  Model: <model> | Threshold: <threshold>
  Findings (above threshold): <list or 'None'>
  ⚠ Critical findings: <Pneumothorax/Pneumonia/Edema/Consolidation if present, else 'None'>
  Top scores: <pathology>=<score>, <pathology>=<score>, <pathology>=<score>
  All scores: <full sorted list>
""",
        model=LOCAL_MODEL,
        tools=[run_xray_classification],
        tool_timeout=300,
    )
    done("XRayImagingAgent ready")
    return agent


# ── AgentAsTool wrappers ──────────────────────────────────────────────────────
# The RouterAgent doesn't call CT/MRI/X-Ray agents directly — it calls them as tools.
# Each wrapper is a plain Python async function the Router LLM can invoke.

def build_ct_agent_tool():
    ct_agent = build_ct_agent()

    async def ct_agent_tool(
        image_path: str,
        output_dir: str,
        roi_subset: str = "liver spleen kidney_right kidney_left aorta",
        fast: bool = False,
        gt_dir: str = "",
    ) -> str:
        """
        Run the full CT imaging pipeline: organ segmentation + optional evaluation.
        Use this when the input image is a CT scan (.nii.gz).

        Args:
            image_path: Absolute path to CT NIfTI (.nii.gz) file
            output_dir: Directory to save segmentation outputs
            roi_subset: Space-separated organ names to segment
            fast:       True = faster 3mm model, False = full resolution
            gt_dir:     Ground truth directory for evaluation (empty = skip eval)

        Returns:
            Summary string with segmentation results and evaluation metrics if run.
        """
        step(f"CT pipeline starting  image={Path(image_path).name}")
        task_str = f"""EXECUTE immediately:
1. Run segmentation:
   image_path: {image_path}
   output_dir: {output_dir}
   task: total
   roi_subset: {roi_subset}
   fast: {fast}
   statistics: True
   gpu: 1
2. {"Run evaluation with gt_dir: " + gt_dir if gt_dir else "Skip evaluation — no gt_dir provided."}
3. Report results.
"""
        result = await ct_agent.run(task_str)
        step("Generating CT report")
        done("CT pipeline complete")
        return result.content

    return ct_agent_tool


def build_mri_agent_tool():
    mri_agent = build_mri_agent()

    async def mri_agent_tool(
        image_path: str,
        output_dir: str,
        roi_subset: str = "pancreas stomach liver spleen",
        fast: bool = True,
        gt_dir: str = "",
    ) -> str:
        """
        Run the full MRI imaging pipeline: organ segmentation + optional evaluation.
        Use this when the input image is an MRI scan (.nii.gz).

        Args:
            image_path: Absolute path to MRI NIfTI (.nii.gz) file
            output_dir: Directory to save segmentation outputs
            roi_subset: Space-separated organ names (MRI supports ~50 structures)
            fast:       True = faster 3mm model (recommended for MRI), False = full resolution
            gt_dir:     Ground truth directory for evaluation (empty = skip eval)

        Returns:
            Summary string with segmentation results and evaluation metrics if run.
        """
        step(f"MRI pipeline starting  image={Path(image_path).name}")
        task_str = f"""EXECUTE immediately:
1. Run segmentation:
   image_path: {image_path}
   output_dir: {output_dir}
   task: total_mr
   roi_subset: {roi_subset}
   fast: {fast}
   statistics: True
   gpu: 1
2. {"Run evaluation with gt_dir: " + gt_dir if gt_dir else "Skip evaluation — no gt_dir provided."}
3. Report results.
"""
        result = await mri_agent.run(task_str)
        step("Generating MRI report")
        done("MRI pipeline complete")
        return result.content

    return mri_agent_tool


def build_xray_agent_tool():
    xray_agent = build_xray_agent()

    async def xray_agent_tool(
        image_path: str,
        output_dir: str = "",
        model: str = "densenet121-res224-all",
        threshold: float = 0.5,
    ) -> str:
        """
        Run chest X-ray pathology classification using TorchXRayVision DenseNet121.
        Use this when the input image is a 2D chest X-ray (PNG, JPG, or DICOM .dcm).
        Do NOT use for CT or MRI scans.

        Available models:
          densenet121-res224-all      best general purpose (recommended)
          densenet121-res224-nih      NIH ChestX-ray8
          densenet121-res224-chex     CheXpert / Stanford
          densenet121-res224-rsna     RSNA Pneumonia Challenge
          resnet50-res512-all         higher resolution, slower, most accurate

        Args:
            image_path: Path to chest X-ray image (PNG, JPG, or .dcm)
            output_dir: Directory to save result JSON (optional)
            model:      TorchXRayVision pretrained model weights
            threshold:  Score threshold for flagging findings (default 0.5)

        Returns:
            Summary string with 18 pathology scores, findings above threshold,
            and critical findings (Pneumothorax, Pneumonia, Edema, Consolidation).
        """
        step(f"X-ray pipeline starting  image={Path(image_path).name}  model={model}")
        task_str = f"""EXECUTE immediately:
Run X-ray classification:
   image_path: {image_path}
   output_dir: {output_dir}
   model: {model}
   threshold: {threshold}
   gpu: 1
Report results.
"""
        result = await xray_agent.run(task_str)
        step("Generating X-ray report")
        done("X-ray pipeline complete")
        return result.content

    return xray_agent_tool


# ── Router Agent ──────────────────────────────────────────────────────────────

def build_router_agent() -> Agent:
    step("Building RouterAgent  (CT + MRI + X-ray tools attached)")
    agent = Agent(
        name="RouterAgent",
        icon="🏥",
        instructions="""
You are a medical imaging router. Your only job is to detect the modality of the
input image and delegate to the correct specialist agent tool.

MODALITY DETECTION — in order of priority:
1. Explicit flag: if the task says "modality: CT", "modality: MRI", or
   "modality: XRAY", use that — always.
2. File extension:
   - .png  .jpg  .jpeg  .dcm              → XRAY
   - .nii  .nii.gz                        → CT or MRI (check filename next)
3. Filename keywords for NIfTI files:
   - Contains "CT", "_ct_", "ct_case", "_0000"          → CT
   - Contains "MR", "MRI", "mri", "amos", "T1", "T2",
     "FLAIR", "DWI"                                      → MRI
4. If still ambiguous: default to CT and note the assumption in your output.

ROUTING RULES:
- CT detected   → call ct_agent_tool   with the provided parameters
- MRI detected  → call mri_agent_tool  with the provided parameters
- XRAY detected → call xray_agent_tool with the provided parameters
- NEVER call more than one tool for the same image.
- NEVER run segmentation or classification yourself — always delegate.
- NEVER ask the user for confirmation.
- Pass gt_dir as an empty string "" if not provided — do not omit it for CT/MRI.

OUTPUT FORMAT:
  Detected modality: <CT | MRI | X-Ray>  (reason: <why>)
  <paste the specialist agent's output here verbatim>
""",
        model=LOCAL_MODEL,
        tools=[
            build_ct_agent_tool(),
            build_mri_agent_tool(),
            build_xray_agent_tool(),
        ],
        tool_timeout=3600,
    )
    done("RouterAgent ready")
    return agent


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_router(
    image_path: str,
    output_dir: str,
    modality: str | None,
    roi_subset: list[str] | None,
    fast: bool,
    gt_dir: str | None,
    xray_model: str,
    xray_threshold: float,
) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    roi = " ".join(roi_subset) if roi_subset else ""

    # Pre-detect XRAY from extension so the router hint is unambiguous
    ext = Path(image_path).suffix.lower()
    if modality is None and ext in (".png", ".jpg", ".jpeg", ".dcm"):
        modality = "XRAY"

    task_str = f"""Process this medical image:

image_path: {image_path}
output_dir: {output_dir}
{f"modality: {modality.upper()}" if modality else "modality: auto-detect from filename"}
roi_subset: {roi if roi else "use modality defaults"}
fast: {fast}
gt_dir: {gt_dir or ""}
xray_model: {xray_model}
xray_threshold: {xray_threshold}

Detect the modality, delegate to the correct specialist agent, report results.
"""

    agent = build_router_agent()

    print(f"\n{'='*60}")
    print(f"Image:    {image_path}")
    print(f"Output:   {output_dir}")
    print(f"Modality: {modality or 'auto-detect'}")
    if modality == "XRAY":
        print(f"Model:    {xray_model}  |  Threshold: {xray_threshold}")
    else:
        print(f"Organs:   {roi or 'modality defaults'}")
        print(f"Fast:     {fast}")
        if gt_dir:
            print(f"GT:       {gt_dir}")
    print(f"{'='*60}\n")

    step("RouterAgent detecting modality and planning pipeline")
    result = await agent.run(task_str)
    step("Finalising output")
    done("Pipeline complete")

    print(f"\n{'='*60}")
    print("RESULT:")
    print(f"{'='*60}")
    print(result.content)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-modal medical imaging agent — CT / MRI / X-Ray",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--image",
        default="datasets/data_exp_ct-mr/CT_case00002/CT_Case_00002_0000.nii.gz",
        help="Input image (.nii.gz for CT/MRI, .png/.jpg/.dcm for X-ray)",
    )
    parser.add_argument(
        "--output_dir",
        default="tmp/test_agent_output",
        help="Output directory for segmentation masks / classification results",
    )
    parser.add_argument(
        "--modality",
        default=None,
        choices=["ct", "mri", "xray", "CT", "MRI", "XRAY"],
        help="Force modality. Auto-detected from filename if omitted.",
    )
    # CT / MRI options
    parser.add_argument(
        "--roi_subset",
        nargs="+",
        default=None,
        help="CT/MRI: organs to segment (space-separated). Uses modality defaults if omitted.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="CT/MRI: use 3mm fast model",
    )
    parser.add_argument(
        "--gt_dir",
        default=None,
        help="CT/MRI: ground truth directory for Dice evaluation (optional)",
    )
    # X-Ray options
    parser.add_argument(
        "--xray_model",
        default="densenet121-res224-all",
        choices=[
            "densenet121-res224-all", "densenet121-res224-rsna",
            "densenet121-res224-nih",  "densenet121-res224-pc",
            "densenet121-res224-chex", "densenet121-res224-mimic_nb",
            "densenet121-res224-mimic_ch", "resnet50-res512-all",
        ],
        help="X-ray: TorchXRayVision pretrained model",
    )
    parser.add_argument(
        "--xray_threshold",
        type=float,
        default=0.5,
        help="X-ray: score threshold for flagging findings (0.0-1.0)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run_router(
        image_path=args.image,
        output_dir=args.output_dir,
        modality=args.modality,
        roi_subset=args.roi_subset,
        fast=args.fast,
        gt_dir=args.gt_dir,
        xray_model=args.xray_model,
        xray_threshold=args.xray_threshold,
    ))


if __name__ == "__main__":
    main()