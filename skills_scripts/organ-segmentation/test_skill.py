#!/usr/bin/env python3
"""
Quick validation test for organ-segmentation skill.
Run this BEFORE wiring into PantheonOS to confirm the skill works.

Usage:
    python scripts/test_skill.py --input /path/to/your/ct.nii.gz
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

def test_skill(image_path: str, gpu: str = "0"):
    print("=" * 60)
    print("organ-segmentation skill — standalone test")
    print("=" * 60)

    script = Path(__file__).parent / "run_segmentation.py"
    output_dir = Path("tmp/organ_seg_test")

    # ── Test 1: fast mode on subset (quickest validation) ──────────────────
    print("\n[TEST 1] Fast mode, liver + spleen only...")
    cmd = [
        sys.executable, str(script),
        "--input",      image_path,
        "--output",     str(output_dir / "test1"),
        "--task",       "total",
        "--fast",
        "--ml",
        "--statistics",
        "--roi_subset", "liver", "spleen",
        "--gpu",        gpu,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  FAILED")
        print(f"  stderr: {result.stderr[:500]}")
        return False

    try:
        output = json.loads(result.stdout)
        print(f"status:           {output['status']}")
        print(f"structures found: {output['structures_found']}")
        print(f"n_structures:     {output['n_structures']}")
        print(f"seg files:        {len(output['segmentation_files'])} files")
        if output.get("statistics"):
            print(f"statistics:       {list(output['statistics'].keys())[:3]}...")
        print(f"result.json:      {output_dir}/test1/result.json")
    except json.JSONDecodeError:
        print(f"Output is not valid JSON:")
        print(result.stdout[:300])
        return False

    print("\n[TEST 2] Checking result.json was written to disk...")
    result_file = output_dir / "test1" / "result.json"
    if result_file.exists():
        print(f"result.json exists at {result_file}")
    else:
        print(f"result.json NOT found at {result_file}")
        return False

    print("\n" + "=" * 60)
    print("All tests passed — skill is ready for PantheonOS")
    print("=" * 60)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to test CT NIfTI file")
    parser.add_argument("--gpu", default="0", help="GPU index to use")
    args = parser.parse_args()

    success = test_skill(args.input, args.gpu)
    sys.exit(0 if success else 1)
