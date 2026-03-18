#!/usr/bin/env python3
"""
Author: Meng Wei

Chest X-Ray Classification — TorchXRayVision wrapper

Classifies 18 pathologies from chest X-ray images using pretrained DenseNet121
or ResNet50 models from TorchXRayVision.

Available models:
  densenet121-res224-all    trained on all datasets (best general purpose)
  densenet121-res224-rsna   RSNA Pneumonia Challenge
  densenet121-res224-nih    NIH ChestX-ray8
  densenet121-res224-pc     PadChest
  densenet121-res224-chex   CheXpert (Stanford)
  densenet121-res224-mimic_nb  MIMIC-CXR
  densenet121-res224-mimic_ch  MIMIC-CXR
  resnet50-res512-all       ResNet50 512x512 (higher res, slower)

Outputs 18 pathology scores (sigmoid probabilities 0-1):
  Atelectasis, Consolidation, Infiltration, Pneumothorax, Edema,
  Emphysema, Fibrosis, Effusion, Pneumonia, Pleural_Thickening,
  Cardiomegaly, Nodule, Mass, Hernia, Lung Lesion, Fracture,
  Lung Opacity, Enlarged Cardiomediastinum

Usage:
    python run_xray_classification.py --input xray.png
    python run_xray_classification.py --input xray.png --model densenet121-res224-nih
    python run_xray_classification.py --input xray.png --threshold 0.5 --gpu 0
"""

import argparse
import json
import sys
from pathlib import Path


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_MODELS = [
    "densenet121-res224-all",
    "densenet121-res224-rsna",
    "densenet121-res224-nih",
    "densenet121-res224-pc",
    "densenet121-res224-chex",
    "densenet121-res224-mimic_nb",
    "densenet121-res224-mimic_ch",
    "resnet50-res512-all",
]

# Clinical significance thresholds (> this = flag as finding)
DEFAULT_THRESHOLD = 0.5

# Critical findings that should always be flagged even at lower confidence
CRITICAL_PATHOLOGIES = {
    "Pneumothorax",
    "Pneumonia",
    "Edema",
    "Consolidation",
}


# ── Image loading & preprocessing ─────────────────────────────────────────────

def load_and_preprocess(image_path: str, model_weights: str):
    """
    Load image and apply TorchXRayVision preprocessing pipeline.

    Handles: PNG, JPG, DICOM (.dcm)
    Converts to grayscale, normalizes to [-1024, 1024], resizes to model input size.
    """
    import numpy as np
    import torch
    import torchvision
    import torchxrayvision as xrv

    path = Path(image_path)

    # ── Load image ────────────────────────────────────────────────────────────
    if path.suffix.lower() == ".dcm":
        try:
            import pydicom
            dcm = pydicom.dcmread(str(path))
            img = dcm.pixel_array.astype(np.float32)
            # Normalize DICOM pixel values
            img = xrv.datasets.normalize(img, img.max())
        except ImportError:
            raise RuntimeError("pydicom required for DICOM files: pip install pydicom")
    else:
        import skimage.io
        img = skimage.io.imread(str(image_path))
        img = xrv.datasets.normalize(img, 255)  # → [-1024, 1024]

    # ── Convert to single channel ─────────────────────────────────────────────
    if img.ndim == 3 and img.shape[2] == 4:
        img = img[:, :, :3]          # drop alpha
    if img.ndim == 3:
        img = img.mean(2)            # RGB → grayscale
    if img.ndim == 2:
        img = img[None, ...]         # add channel dim → (1, H, W)

    # ── Resize to match model input ───────────────────────────────────────────
    resize = 512 if "res512" in model_weights else 224
    transform = torchvision.transforms.Compose([
        xrv.datasets.XRayCenterCrop(),
        xrv.datasets.XRayResizer(resize),
    ])
    img = transform(img)

    # ── To tensor ────────────────────────────────────────────────────────────
    img = torch.from_numpy(img)      # (1, H, W)
    return img


# ── Model loading ─────────────────────────────────────────────────────────────

def load_model(weights: str, gpu: int):
    """Load TorchXRayVision model, move to correct device."""
    import torch
    import torchxrayvision as xrv

    if "resnet50" in weights:
        model = xrv.models.ResNet(weights=weights)
    else:
        model = xrv.models.DenseNet(weights=weights)

    device = torch.device(f"cuda:{gpu}" if gpu >= 0 else "cpu")
    model = model.to(device)
    model.eval()
    return model, device


# ── Inference ─────────────────────────────────────────────────────────────────

def run_inference(model, img_tensor, device, threshold: float):
    """Run forward pass, return structured results."""
    import torch
    import numpy as np

    img_tensor = img_tensor.to(device)

    with torch.no_grad():
        outputs = model(img_tensor[None, ...])   # add batch dim → (1, 1, H, W)

    scores = outputs[0].cpu().numpy()
    pathologies = model.pathologies

    # Build per-pathology results
    results = {}
    findings = []
    critical_findings = []

    for pathology, score in zip(pathologies, scores):
        score_f = float(score)
        results[pathology] = round(score_f, 4)

        if score_f >= threshold:
            findings.append(pathology)
            if pathology in CRITICAL_PATHOLOGIES:
                critical_findings.append(pathology)

    # Sort by score descending for easy reading
    results_sorted = dict(
        sorted(results.items(), key=lambda x: x[1], reverse=True)
    )

    return results_sorted, findings, critical_findings


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Chest X-Ray pathology classification using TorchXRayVision"
    )
    parser.add_argument("--input",  "-i", required=True,
                        help="Path to chest X-ray image (PNG, JPG, or DICOM .dcm)")
    parser.add_argument("--model",  "-m", default="densenet121-res224-all",
                        choices=VALID_MODELS,
                        help="Pretrained model weights to use")
    parser.add_argument("--threshold", "-t", type=float, default=DEFAULT_THRESHOLD,
                        help="Score threshold for flagging findings (default: 0.5)")
    parser.add_argument("--gpu",    "-g", type=int, default=0,
                        help="GPU index (-1 for CPU)")
    parser.add_argument("--output", "-o", default=None,
                        help="Path to write result JSON (optional)")
    args = parser.parse_args()

    # ── Validate input ────────────────────────────────────────────────────────
    input_path = Path(args.input)
    if not input_path.exists():
        result = {"status": "error", "error": f"Input not found: {args.input}"}
        print(json.dumps(result))
        sys.exit(1)

    # ── Run pipeline ─────────────────────────────────────────────────────────
    print(f"[xray-classification] Loading image: {args.input}", file=sys.stderr)
    print(f"[xray-classification] Model: {args.model}", file=sys.stderr)

    try:
        img = load_and_preprocess(args.input, args.model)
    except Exception as e:
        result = {"status": "error", "error": f"Image loading failed: {e}"}
        print(json.dumps(result))
        sys.exit(1)

    print(f"[xray-classification] Running inference on GPU {args.gpu}...", file=sys.stderr)

    try:
        model, device = load_model(args.model, args.gpu)
        scores, findings, critical = run_inference(model, img, device, args.threshold)
    except Exception as e:
        result = {"status": "error", "error": f"Inference failed: {e}"}
        print(json.dumps(result))
        sys.exit(1)

    # ── Build output ──────────────────────────────────────────────────────────
    result = {
        "status":             "success",
        "image_path":         str(args.input),
        "model":              args.model,
        "threshold":          args.threshold,
        "scores":             scores,           # all 18 pathologies, sorted by score
        "findings":           findings,          # pathologies above threshold
        "critical_findings":  critical,          # subset: pneumothorax, pneumonia, etc.
        "n_findings":         len(findings),
        "top_3": list(scores.keys())[:3],        # top 3 by score regardless of threshold
    }

    # ── Optional: save to file ────────────────────────────────────────────────
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"[xray-classification] Results saved to {args.output}", file=sys.stderr)

    # ── Print summary to stderr ───────────────────────────────────────────────
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"Findings (>{args.threshold}): {findings or 'None'}", file=sys.stderr)
    if critical:
        print(f"⚠ CRITICAL: {critical}", file=sys.stderr)
    print(f"Top scores:", file=sys.stderr)
    for path, score in list(scores.items())[:5]:
        bar = "-" * int(score * 20)
        print(f"  {path:<35} {score:.4f}  {bar}", file=sys.stderr)

    # Print JSON to stdout — agent reads this
    print(json.dumps(result))


if __name__ == "__main__":
    main()
