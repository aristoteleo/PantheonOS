"""
Medical Imaging Demo — Natural Language CLI
============================================

Wraps med_agent.py with a natural language front-end.
One extra step before run_router(): parse the user's free-text task into
structured parameters, then pass everything (including the original text)
straight to run_router().

Flow:
    demo.py
      │
      ├─ parse_task_from_text()     ← single Ollama call, no agent, ~2s
      │       extracts: modality, roi_subset, fast, xray_model, xray_threshold
      │
      └─ run_router()               ← from med_agent.py, unchanged
              task_str includes structured hints + original user text
              RouterAgent → specialist agent → actual script on GPU

Usage:
    python demo.py --image scan.nii.gz --task "segment this CT scan please"
    python demo.py --image xray.png   --task "classify chest x-ray, worried about pneumonia"
    python demo.py --image mri.nii.gz --task "quick MRI of pancreas and stomach"
    python demo.py --image ct.nii.gz  --task "liver segmentation" --gt_dir ./gt
    python demo.py --image ct.nii.gz  --task "fast CT liver seg" --dry_run
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Env must be set before pantheon import (med_agent imports pantheon) ───────
os.environ.setdefault("LLM_API_BASE", "http://localhost:11434/v1")
os.environ.setdefault("LLM_API_KEY",  "ollama")

# ── Import from med_agent.py (same directory) ─────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from med_agent_cmx import run_router, LOCAL_MODEL, step, done, fail  # noqa: E402


# ── NLP parser ────────────────────────────────────────────────────────────────

PARSE_SYSTEM_PROMPT = """\
You are a medical imaging parameter extractor.
Given a user's natural language task and an image filename, extract structured
parameters. Respond ONLY with a single valid JSON object — no markdown, no explanation.

JSON schema (all fields required):
{
  "modality":        "CT" | "MRI" | "XRAY" | "auto",
  "roi_subset":      "<space-separated organ names>" | "",
  "fast":            true | false,
  "xray_model":      "densenet121-res224-all" | "densenet121-res224-rsna" |
                     "densenet121-res224-nih"  | "densenet121-res224-chex" |
                     "densenet121-res224-mimic_nb" | "densenet121-res224-mimic_ch" |
                     "resnet50-res512-all",
  "xray_threshold":  <float 0.0-1.0>,
  "intent_summary":  "<one sentence: what the user wants>"
}

RULES:

modality:
  .png / .jpg / .jpeg / .dcm extension → always "XRAY"
  "CT", "computed tomography", "cat scan" → "CT"
  "MRI", "MR", "magnetic resonance", "T1", "T2" → "MRI"
  "chest x-ray", "CXR", "x-ray", "radiograph" → "XRAY"
  .nii / .nii.gz with no keywords → "auto"

roi_subset (use TotalSegmentator organ names, space-separated):
  "liver" → "liver"
  "spleen" → "spleen"
  "kidney/kidneys" → "kidney_right kidney_left"
  "aorta" → "aorta"
  "lung/lungs" → "lung_upper_lobe_left lung_upper_lobe_right lung_lower_lobe_left lung_lower_lobe_right"
  "pancreas" → "pancreas"
  "stomach" → "stomach"
  "heart" → "heart"
  "all / full / everything / complete" → ""
  XRAY modality → always ""

fast:
  "quick", "fast", "rapid", "3mm", "preview" → true
  "full resolution", "accurate", "precise", "high quality" → false
  no signal → false

xray_model:
  "pneumonia", "rsna" → "densenet121-res224-rsna"
  "NIH", "nih" → "densenet121-res224-nih"
  "stanford", "chexpert" → "densenet121-res224-chex"
  "high resolution", "best", "512" → "resnet50-res512-all"
  no signal → "densenet121-res224-all"

xray_threshold:
  "sensitive", "catch more", "don't miss" → 0.3
  "very sensitive" → 0.2
  "specific", "confident only", "fewer false positives" → 0.6
  "very specific" → 0.7
  no signal → 0.5
"""


def parse_task_from_text(task: str, image_path: str) -> dict:
    """
    Single Ollama call (no PantheonOS agent, no tools) that extracts structured
    parameters from free-text. Fast (~2s), used only to pre-parse before run_router.
    """
    import urllib.request

    ext = Path(image_path).suffix.lower()
    user_msg = f'Image filename: "{Path(image_path).name}"\nUser task: "{task}"'

    payload = json.dumps({
        "model":      LOCAL_MODEL,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": PARSE_SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
    }).encode()

    api_base = os.environ.get("LLM_API_BASE", "http://localhost:11434/v1")
    url      = api_base.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'ollama')}",
    })

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        raw = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        fail(f"NLP parse failed: {e} — using defaults")
        return _defaults(image_path)

    # Strip markdown fences if model added them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        params = json.loads(raw)
    except json.JSONDecodeError:
        fail(f"Could not parse NLP JSON: {raw[:200]} — using defaults")
        return _defaults(image_path)

    # Extension always wins for XRAY
    if ext in (".png", ".jpg", ".jpeg", ".dcm"):
        params["modality"] = "XRAY"
    if params.get("modality") == "XRAY":
        params["roi_subset"] = ""

    params.setdefault("modality",       "auto")
    params.setdefault("roi_subset",     "")
    params.setdefault("fast",           False)
    params.setdefault("xray_model",     "densenet121-res224-all")
    params.setdefault("xray_threshold", 0.5)
    params.setdefault("intent_summary", task)
    return params


def _defaults(image_path: str) -> dict:
    ext = Path(image_path).suffix.lower()
    return {
        "modality":       "XRAY" if ext in (".png", ".jpg", ".jpeg", ".dcm") else "auto",
        "roi_subset":     "",
        "fast":           False,
        "xray_model":     "densenet121-res224-all",
        "xray_threshold": 0.5,
        "intent_summary": "(NLP parse failed — using defaults)",
    }


def print_parse_result(params: dict, image_path: str, output_dir: str) -> None:
    mod = params["modality"]
    print(f"\n{'─'*60}")
    print("  NLP PARSE RESULT")
    print(f"{'─'*60}")
    print(f"  Intent:   {params['intent_summary']}")
    print(f"  Modality: {mod}")
    if mod == "XRAY":
        print(f"  Model:    {params['xray_model']}")
        print(f"  Threshold:{params['xray_threshold']}")
    else:
        print(f"  Organs:   {params['roi_subset'] or '(modality defaults)'}")
        print(f"  Fast:     {params['fast']}")
    print(f"  Image:    {image_path}")
    print(f"  Output:   {output_dir}")
    print(f"{'─'*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Medical imaging demo — natural language CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo.py --image ct.nii.gz \\
      --task "segment the liver and kidneys from this CT scan"

  python demo.py --image xray.png \\
      --task "classify this chest x-ray, I'm worried about pneumonia"

  python demo.py --image mri.nii.gz \\
      --task "quick MRI segmentation of pancreas and stomach"

  python demo.py --image ct.nii.gz --gt_dir ./gt \\
      --task "full resolution CT liver segmentation and evaluate accuracy"

  python demo.py --image ct.nii.gz --task "fast CT liver seg" --dry_run
""",
    )
    parser.add_argument("--image",  "-i", required=True,
                        help="Path to input image")
    parser.add_argument("--task",   "-t", required=True,
                        help='Natural language task, e.g. "segment liver from this CT"')
    parser.add_argument("--output_dir", "-o", default=None,
                        help="Output directory. Auto-generated if omitted.")
    parser.add_argument("--gt_dir", default=None,
                        help="Ground truth dir for CT/MRI evaluation (overrides NLP)")
    parser.add_argument("--dry_run", action="store_true",
                        help="Show NLP parse result and exit without running pipeline")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not Path(args.image).exists():
        print(f"Error: image not found: {args.image}")
        sys.exit(1)

    # Auto output dir from image stem + timestamp
    stem = Path(args.image).stem.replace(".nii", "")
    output_dir = args.output_dir or str(
        Path("tmp") / "demo" / f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    # ── Step 1: NLP parse ─────────────────────────────────────────────────────
    step("Parsing task with NLP")
    params = parse_task_from_text(args.task, args.image)
    done("Parse complete")

    # CLI --gt_dir always wins over NLP
    gt_dir = args.gt_dir or params.get("gt_dir") or None

    print_parse_result(params, args.image, output_dir)

    if args.dry_run:
        print("Dry run — pipeline not executed.")
        return

    # ── Step 2: Confirm ───────────────────────────────────────────────────────
    print("Press Enter to run  (Ctrl-C to abort)...")
    try:
        input()
    except KeyboardInterrupt:
        print("\nAborted.")
        return

    # ── Step 3: run_router() from med_agent.py ────────────────────────────────
    # roi_subset: str → list[str] | None
    roi_list = params["roi_subset"].split() if params["roi_subset"] else None
    modality = params["modality"] if params["modality"] != "auto" else None

    asyncio.run(run_router(
        image_path    = args.image,
        output_dir    = output_dir,
        modality      = modality,
        roi_subset    = roi_list,
        fast          = params["fast"],
        gt_dir        = gt_dir,
        xray_model    = params["xray_model"],
        xray_threshold= params["xray_threshold"],
        user_task     = args.task,       # ← passed into task_str as context
    ))


if __name__ == "__main__":
    main()