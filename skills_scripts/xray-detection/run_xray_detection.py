#!/usr/bin/env python3
"""
Author: Meng Wei

Chest X-Ray detection

automatic detection of carina and ETT (endotracheal tube) in chest X-rays

Available models: 
  RetinaNet with ResNet backbone

INputs: Images given in input should be chest X-ray radiographs of sufficient resolution on which the endotracheal tube should be visible.

Output: Coordinates for carina and ETT locations

Usage:
    python run_xray_detection.py --input xray.png
"""

import argparse
import json
import sys
from pathlib import Path




# ── Model loading ─────────────────────────────────────────────────────────────

def load_model(gpu: int):
    """Load TorchXRayVision model, move to correct device."""
    import torch
    import carinanet

    model = carinanet.CarinaNetModel()

    device = torch.device(f"cuda:{gpu}" if gpu >= 0 else "cpu")
    # model = model.to(device)
    # model.eval()
    return model, device


# ── Inference ─────────────────────────────────────────────────────────────────

def run_inference(model, img_path, device):
    """Run forward pass, return structured results."""
    import torch
    import numpy as np
    import math

    image_path = Path(img_path)
    outputs = model.predict(image_path)
    


    carina_detected = outputs.get('carina') is not None
    carina_pixel = outputs['carina'] if carina_detected else None

    ett_detected = outputs.get('ett') is not None
    ett_pixel = outputs['ett'] if ett_detected else None

    carina_confident  = carina_detected and outputs.get('carina_confidence')
    ett_confident     = ett_detected and outputs.get('ett_confidence')

    pixel_dist = math.dist(ett_pixel, carina_pixel) if ett_pixel is not None and carina_pixel is not None else None

    results = {}

    results['carina_detected'] = carina_detected
    results['ett_detected'] = ett_detected
    results['carina_pixel'] = carina_pixel
    results['ett_pixel'] = ett_pixel
    results['carina_confident'] = carina_confident
    results['ett_confident'] = ett_confident

    return results, pixel_dist


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Chest X-Ray detection of carina and ETT (endotracheal tube)"
    )
    parser.add_argument("--input",  "-i", required=True,
                        help="Path to chest X-ray image (PNG, JPG, or DICOM .dcm)")
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
    print(f"[xray-carina and endotracheal tubedetection-detection] Loading image: {args.input}", file=sys.stderr)

    print(f"[xray-carina and endotracheal tubedetection-detection] Running inference on GPU {args.gpu}...", file=sys.stderr)

    try:
        model, device = load_model(args.gpu)
        results, pixel_dist = run_inference(model, args.input, device)
    except Exception as e:
        result = {"status": "error", "error": f"Inference failed: {e}"}
        print(json.dumps(result))
        sys.exit(1)

    # ── Build output ──────────────────────────────────────────────────────────
    result = {
        "status":             "success",
        "image_path":         str(args.input),
        "results":            results,           #results dict with carina and ETT detection info
        "pixel_dist":         pixel_dist,        #distance between carina and ETT pixels
    }

    # ── Optional: save to file ────────────────────────────────────────────────
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"[xray-carina and endotracheal tubedetection-detection] Results saved to {args.output}", file=sys.stderr)

    # ── Print summary to stderr ───────────────────────────────────────────────
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"Detected results: {results or 'None'}", file=sys.stderr)
    print(f"Pixel distance between ETT tip and carina: {pixel_dist}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
