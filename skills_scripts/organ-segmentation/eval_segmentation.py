#!/usr/bin/env python3
"""
Author: Meng Wei

Organ Segmentation Evaluation — Dice + HD95 per organ.

Compares predicted masks from run_segmentation.py against ground truth masks.
Writes eval.json to output_dir — used by Pantheon-Evolve as fitness signal.

Usage:
    # Evaluate one case
    python eval_segmentation.py \
        --pred_dir  /path/to/seg_output \
        --gt_dir    /path/to/ground_truth \
        --output    /path/to/eval.json

    # Evaluate multiple cases and get mean scores
    python eval_segmentation.py \
        --cases_dir /path/to/cases \
        --output    /path/to/eval_summary.json

Directory layout expected:
    cases_dir/
    ├── case_001/
    │   ├── pred/          ← output from run_segmentation.py
    │   │   ├── liver.nii.gz
    │   │   └── kidney_right.nii.gz
    │   └── gt/            ← your ground truth masks
    │       ├── liver.nii.gz
    │       └── kidney_right.nii.gz
    └── case_002/
        ├── pred/
        └── gt/
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import nibabel as nib


# ── Metrics ──────────────────────────────────────────────────────────────────

def dice_score(pred: np.ndarray, gt: np.ndarray) -> float:
    """
    Compute Dice Similarity Coefficient.
    Returns 1.0 if both masks are empty (both correctly empty).
    Returns 0.0 if only one is empty.
    """
    pred = pred.astype(bool)
    gt   = gt.astype(bool)

    # Both empty → perfect score
    if not pred.any() and not gt.any():
        return 1.0

    # One empty, one not → zero
    if not pred.any() or not gt.any():
        return 0.0

    intersection = (pred & gt).sum()
    return float(2.0 * intersection / (pred.sum() + gt.sum()))


def hausdorff_95(pred: np.ndarray, gt: np.ndarray, voxel_spacing: tuple) -> float:
    """
    Compute 95th percentile Hausdorff Distance in mm.
    Uses surface-distance library (same as TotalSegmentator paper).
    Returns 0.0 if both empty, inf if one is empty.
    """
    try:
        import surface_distance as sd
    except ImportError:
        # Fallback: return -1 if library not available
        return -1.0

    pred = pred.astype(bool)
    gt   = gt.astype(bool)

    if not pred.any() and not gt.any():
        return 0.0
    if not pred.any() or not gt.any():
        return float("inf")

    distances = sd.compute_surface_distances(gt, pred, spacing_mm=voxel_spacing)
    hd95 = sd.compute_robust_hausdorff(distances, percent=95)
    return float(hd95)


def volume_mm3(mask: np.ndarray, voxel_spacing: tuple) -> float:
    """Compute volume in mm³ from binary mask."""
    voxel_vol = float(np.prod(voxel_spacing))
    return float(mask.astype(bool).sum() * voxel_vol)


# ── I/O helpers ───────────────────────────────────────────────────────────────

def load_mask(path: Path) -> tuple[np.ndarray, tuple]:
    """Load NIfTI mask, return (array, voxel_spacing_mm)."""
    img = nib.load(str(path))
    data = np.asarray(img.dataobj).astype(np.uint8)
    spacing = tuple(float(v) for v in img.header.get_zooms()[:3])
    return data, spacing


def find_organ_pairs(pred_dir: Path, gt_dir: Path) -> list[tuple[str, Path, Path]]:
    """
    Match predicted masks with ground truth masks by filename.
    Returns list of (organ_name, pred_path, gt_path).
    """
    pred_masks = {f.stem.replace(".nii", ""): f
                  for f in pred_dir.glob("*.nii.gz")}
    gt_masks   = {f.stem.replace(".nii", ""): f
                  for f in gt_dir.glob("*.nii.gz")}

    # Only evaluate organs present in both
    common = sorted(set(pred_masks) & set(gt_masks))
    pairs  = [(organ, pred_masks[organ], gt_masks[organ]) for organ in common]

    missing_pred = set(gt_masks) - set(pred_masks)
    missing_gt   = set(pred_masks) - set(gt_masks)

    if missing_pred:
        print(f"  ⚠ No prediction for: {sorted(missing_pred)} — skipped",
              file=sys.stderr)
    if missing_gt:
        print(f"  ⚠ No ground truth for: {sorted(missing_gt)} — skipped",
              file=sys.stderr)

    return pairs


# ── Single-case evaluation ────────────────────────────────────────────────────

def evaluate_case(pred_dir: Path, gt_dir: Path) -> dict:
    """
    Evaluate all organs for one case.
    Returns per-organ metrics + case-level means.
    """
    pairs = find_organ_pairs(pred_dir, gt_dir)

    if not pairs:
        return {
            "status": "error",
            "error": f"No matching masks between {pred_dir} and {gt_dir}",
            "organs": {},
            "mean_dice": 0.0,
            "mean_hd95": float("inf"),
        }

    organs = {}
    dice_scores = []
    hd95_scores = []

    for organ, pred_path, gt_path in pairs:
        print(f"  evaluating: {organ}", file=sys.stderr)

        pred, pred_spacing = load_mask(pred_path)
        gt,   gt_spacing   = load_mask(gt_path)

        # Use GT spacing as reference (more reliable)
        spacing = gt_spacing

        # Compute metrics
        dc   = dice_score(pred, gt)
        hd   = hausdorff_95(pred, gt, spacing)
        vol_pred = volume_mm3(pred, spacing)
        vol_gt   = volume_mm3(gt,   spacing)

        organs[organ] = {
            "dice":          round(dc,  4),
            "hd95_mm":       round(hd,  2) if hd != float("inf") else "inf",
            "volume_pred_ml": round(vol_pred / 1000, 2),
            "volume_gt_ml":   round(vol_gt   / 1000, 2),
            "pred_path":     str(pred_path),
            "gt_path":       str(gt_path),
        }

        dice_scores.append(dc)
        if hd != float("inf"):
            hd95_scores.append(hd)

    mean_dice = float(np.mean(dice_scores)) if dice_scores else 0.0
    mean_hd95 = float(np.mean(hd95_scores)) if hd95_scores else float("inf")

    return {
        "status":    "success",
        "n_organs":  len(organs),
        "organs":    organs,
        "mean_dice": round(mean_dice, 4),
        "mean_hd95": round(mean_hd95, 2),
    }


# ── Multi-case evaluation ─────────────────────────────────────────────────────

def evaluate_dataset(cases_dir: Path) -> dict:
    """
    Evaluate all cases in a dataset directory.
    Each case must have pred/ and gt/ subdirectories.
    Returns per-case + dataset-level aggregate metrics.
    """
    case_dirs = sorted([
        d for d in cases_dir.iterdir()
        if d.is_dir() and (d / "pred").exists() and (d / "gt").exists()
    ])

    if not case_dirs:
        return {
            "status": "error",
            "error":  f"No valid cases found in {cases_dir}. "
                      f"Each case needs pred/ and gt/ subdirs.",
        }

    print(f"Found {len(case_dirs)} cases", file=sys.stderr)

    cases = {}
    all_dice = []
    all_hd95 = []

    for case_dir in case_dirs:
        print(f"\nCase: {case_dir.name}", file=sys.stderr)
        result = evaluate_case(case_dir / "pred", case_dir / "gt")
        cases[case_dir.name] = result

        if result["status"] == "success":
            all_dice.append(result["mean_dice"])
            if result["mean_hd95"] != float("inf"):
                all_hd95.append(result["mean_hd95"])

    # Dataset-level aggregates
    dataset_dice = float(np.mean(all_dice)) if all_dice else 0.0
    dataset_hd95 = float(np.mean(all_hd95)) if all_hd95 else float("inf")

    # Per-organ means across all cases
    organ_dice_all: dict[str, list] = {}
    for case_result in cases.values():
        for organ, metrics in case_result.get("organs", {}).items():
            organ_dice_all.setdefault(organ, []).append(metrics["dice"])

    per_organ_mean = {
        organ: round(float(np.mean(scores)), 4)
        for organ, scores in organ_dice_all.items()
    }

    return {
        "status":          "success",
        "n_cases":         len(case_dirs),
        "dataset_dice":    round(dataset_dice, 4),   # ← Pantheon-Evolve fitness signal
        "dataset_hd95":    round(dataset_hd95, 2),
        "per_organ_dice":  per_organ_mean,
        "cases":           cases,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate organ segmentation: Dice + HD95 vs ground truth"
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pred_dir",  type=Path,
                      help="Predicted masks dir (single case)")
    mode.add_argument("--cases_dir", type=Path,
                      help="Dataset dir with case_*/pred + case_*/gt structure")

    parser.add_argument("--gt_dir",  type=Path,
                        help="Ground truth masks dir (required with --pred_dir)")
    parser.add_argument("--output",  type=Path, required=True,
                        help="Path to write eval.json")
    parser.add_argument("--organs",  nargs="+",
                        help="Only evaluate specific organs e.g. liver kidney_right")

    args = parser.parse_args()

    # ── Validate ──────────────────────────────────────────────────────────────
    if args.pred_dir and not args.gt_dir:
        parser.error("--gt_dir required when using --pred_dir")

    # ── Run evaluation ────────────────────────────────────────────────────────
    if args.pred_dir:
        print(f"Evaluating single case:", file=sys.stderr)
        print(f"  pred: {args.pred_dir}", file=sys.stderr)
        print(f"  gt:   {args.gt_dir}",   file=sys.stderr)
        result = evaluate_case(args.pred_dir, args.gt_dir)

    else:
        print(f"Evaluating dataset: {args.cases_dir}", file=sys.stderr)
        result = evaluate_dataset(args.cases_dir)

    # ── Write output ──────────────────────────────────────────────────────────
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    # ── Print summary ─────────────────────────────────────────────────────────
    print(json.dumps(result, indent=2))

    if result["status"] == "success":
        if "dataset_dice" in result:
            print(f"\n{'='*40}", file=sys.stderr)
            print(f"Dataset Dice:  {result['dataset_dice']:.4f}", file=sys.stderr)
            print(f"Dataset HD95:  {result['dataset_hd95']:.2f} mm", file=sys.stderr)
            print(f"Cases:         {result['n_cases']}", file=sys.stderr)
            print(f"\nPer-organ Dice:", file=sys.stderr)
            for organ, dice in sorted(result["per_organ_dice"].items()):
                bar = "█" * int(dice * 20)
                print(f"  {organ:<30} {dice:.4f}  {bar}", file=sys.stderr)
        else:
            print(f"\n{'='*40}", file=sys.stderr)
            print(f"Mean Dice:  {result['mean_dice']:.4f}", file=sys.stderr)
            print(f"Mean HD95:  {result['mean_hd95']:.2f} mm", file=sys.stderr)
            print(f"\nPer-organ:", file=sys.stderr)
            for organ, m in sorted(result["organs"].items()):
                bar = "█" * int(m["dice"] * 20)
                print(f"  {organ:<30} Dice={m['dice']:.4f}  HD95={m['hd95_mm']}mm  {bar}",
                      file=sys.stderr)


if __name__ == "__main__":
    main()
