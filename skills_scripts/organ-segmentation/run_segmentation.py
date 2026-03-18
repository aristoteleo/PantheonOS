#!/usr/bin/env python3
"""
Author: Meng Wei

Organ Segmentation Skill — TotalSegmentator wrapper
Part of: multi-modal_medical_agent/skills/organ-segmentation

This script is the single entry point called by PantheonOS agents.
It wraps TotalSegmentator CLI and returns a structured JSON result.

Usage:
    python scripts/run_segmentation.py --input ct.nii.gz --output ./out --task total
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────────────

VALID_TASKS = ["total", "total_mr", "lung_vessels"]

TASK_MODALITY = {
    "total": "CT",
    "lung_vessels": "CT",
    "total_mr": "MRI",
}

# Load class maps from resources
RESOURCES_DIR = Path(__file__).parent.parent / "resources"


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_class_map(task: str) -> dict:
    class_map_path = RESOURCES_DIR / "CT_Seg_TotalSegmentator.json"
    if class_map_path.exists():
        with open(class_map_path) as f:
            all_maps = json.load(f)
        return all_maps.get(task, {})
    return {}


def build_command(args) -> list[str]:
    """Build the TotalSegmentator CLI command from parsed args."""
    cmd = [
        "TotalSegmentator",
        "-i", args.input,
        "-o", args.output,
        "--task", args.task,
    ]

    if args.fast:
        cmd.append("--fast")
    if args.ml:
        cmd.append("--ml")
    if args.statistics:
        cmd.append("--statistics")
    if args.preview:
        cmd.append("--preview")
    if args.radiomics:
        cmd.append("--radiomics")
    if args.roi_subset:
        cmd += ["--roi_subset"] + args.roi_subset
    if args.output_type:
        cmd += ["--output_type", args.output_type]

    return cmd


def collect_outputs(output_dir: Path, args) -> dict:
    """Scan output directory and collect all generated files."""
    output_dir = Path(output_dir)

    # Collect segmentation mask files
    seg_files = sorted([
        str(f) for f in output_dir.glob("*.nii.gz")
        if f.name != "multilabel.nii.gz"
    ])

    # Derive structure names from filenames
    structures_found = [
        Path(f).stem.replace(".nii", "")
        for f in seg_files
    ]

    result = {
        "status": "success",
        "task": args.task,
        "modality": TASK_MODALITY.get(args.task, "CT"),
        "output_dir": str(output_dir),
        "segmentation_files": seg_files,
        "structures_found": structures_found,
        "n_structures": len(structures_found),
        "multilabel_file": None,
        "statistics_file": None,
        "radiomics_file": None,
        "preview_image": None,
        "probabilities_file": None,
        "error": None,
    }

    # Optional output files
    multilabel = output_dir / "multilabel.nii.gz"
    if multilabel.exists():
        result["multilabel_file"] = str(multilabel)

    stats = output_dir / "statistics.json"
    if stats.exists():
        result["statistics_file"] = str(stats)
        # Inline the statistics content for the agent
        with open(stats) as f:
            result["statistics"] = json.load(f)

    radiomics = output_dir / "statistics_radiomics.json"
    if radiomics.exists():
        result["radiomics_file"] = str(radiomics)

    preview = output_dir / "preview.png"
    if preview.exists():
        result["preview_image"] = str(preview)

    probs = output_dir / "probabilities.npz"
    if probs.exists():
        result["probabilities_file"] = str(probs)

    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TotalSegmentator skill wrapper for PantheonOS"
    )

    # Required
    parser.add_argument("--input",  "-i", required=True,
                        help="Path to CT/MRI NIfTI file or DICOM folder/zip")
    parser.add_argument("--output", "-o", required=True,
                        help="Output directory for segmentation results")

    # Task
    parser.add_argument("--task", "-ta", default="total",
                        choices=VALID_TASKS,
                        help="Segmentation task/model to use")

    # Flags matching TotalSegmentator CLI
    parser.add_argument("--fast",       action="store_true",
                        help="Use fast 3mm resolution model")
    parser.add_argument("--ml",         action="store_true",
                        help="Save multilabel NIfTI (all classes in one file)")
    parser.add_argument("--statistics", action="store_true",
                        help="Compute volume (mm³) and mean intensity per structure")
    parser.add_argument("--preview",    action="store_true",
                        help="Generate PNG preview of segmentation")
    parser.add_argument("--radiomics",  action="store_true",
                        help="Compute radiomics features (requires pyradiomics)")
    parser.add_argument("--roi_subset", nargs="+",
                        help="Only segment specific structures e.g. liver kidney_right")
    parser.add_argument("--output_type", default="nifti",
                        choices=["nifti", "dicom"],
                        help="Output file format")

    # GPU control
    parser.add_argument("--gpu", default=None,
                        help="GPU index to use e.g. 0, 1, 2 (default: auto)")

    args = parser.parse_args()

    # ── Validate inputs ───────────────────────────────────────────────────────
    input_path = Path(args.input)
    if not input_path.exists():
        result = {
            "status": "error",
            "error": f"Input file/folder not found: {args.input}",
        }
        print(json.dumps(result))
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── GPU selection ─────────────────────────────────────────────────────────
    env = os.environ.copy()
    if args.gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(args.gpu)

    # ── Build and run command ─────────────────────────────────────────────────
    cmd = build_command(args)

    print(f"[organ-segmentation] Running: {' '.join(cmd)}", file=sys.stderr)

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=3600,   # 1 hour max
        )

        if proc.returncode != 0:
            result = {
                "status": "error",
                "error": proc.stderr or "TotalSegmentator failed with non-zero exit",
                "stdout": proc.stdout,
                "returncode": proc.returncode,
            }
            print(json.dumps(result))
            sys.exit(1)

    except subprocess.TimeoutExpired:
        result = {
            "status": "error",
            "error": "TotalSegmentator timed out after 3600 seconds",
        }
        print(json.dumps(result))
        sys.exit(1)

    except FileNotFoundError:
        result = {
            "status": "error",
            "error": "TotalSegmentator not found. Install with: pip install TotalSegmentator",
        }
        print(json.dumps(result))
        sys.exit(1)

    # ── Collect and return results ────────────────────────────────────────────
    result = collect_outputs(output_dir, args)

    # Write result.json for agent to read
    result_path = output_dir / "result.json"
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2)

    # Print to stdout — PantheonOS agent reads this
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
