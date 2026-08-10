#!/usr/bin/env python3
"""
In-container runner for AHC evaluation.

This script runs INSIDE the Docker container (yimjk/ale-bench:cpp20-202301).
It is invoked by the host evaluator via:
    docker run ... python3 docker_runner.py <cpp_file> <input_dir> <tester_bin> <num_cases> <num_workers> <time_limit>

It performs:
  1. Compile the C++ file with g++-12
  2. Run the binary against all test cases in parallel
  3. Score each case with the tester binary
  4. Print JSON results to stdout

The host evaluator captures stdout and parses the JSON.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


def run_single_case(args):
    """Run solution binary on one test case and score it with the tester."""
    binary_path, case_idx, input_file, tester_path, time_limit = args

    local_tmp = tempfile.mkdtemp(prefix=f"case{case_idx}_")
    try:
        output_file = os.path.join(local_tmp, "output.txt")
        profiles_file = os.path.join(local_tmp, "profiles.json")
        time_limit_ceil = math.ceil(time_limit + 0.1)
        TIMEOUT_MARGIN = 1.5  # seconds — matches TTT
        # Tolerance added to host-side wall-time TLE check, matching ALE-Bench standard.
        # Accounts for Docker/container overhead and measurement differences between
        # local evaluation and official AtCoder evaluation.
        TIME_LIMIT_TOLERANCE = 0.5  # seconds

        # Build run command:
        #   timeout {ceil+1.5} bash -c 'prlimit --cpu={ceil+0.1} /usr/bin/time -f "..." ./a.out < input > output; sync'
        # sync is inside the bash subshell so timeout kills it too — prevents sync zombie processes on TLE
        # prlimit --cpu guarantees CPU time budget regardless of wall-time contention
        time_format = (
            '{"exit_status": "%x", "elapsed_time_seconds": "%e", '
            '"user_cpu_seconds": "%U", "system_cpu_seconds": "%S", '
            '"max_resident_set_size_kbytes": "%M"}'
        )
        cmd = (
            f"timeout {time_limit_ceil + TIMEOUT_MARGIN} "
            f"bash -c 'prlimit --cpu={time_limit_ceil + 0.1} "
            f"/usr/bin/time -f \"{time_format}\" -o {profiles_file} "
            f"{binary_path} {time_limit} < {input_file} > {output_file}; sync'"
        )

        # --- Run solution ---
        start = time.perf_counter()
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                stderr=subprocess.PIPE,
                timeout=time_limit_ceil + TIMEOUT_MARGIN + 2,  # outer safety margin
            )
            elapsed = time.perf_counter() - start
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - start
            return {"case_idx": case_idx, "score": 0, "judge": "TLE",
                    "msg": f"Wall timeout {elapsed:.2f}s", "time": elapsed}

        # Parse profiles if available (for TLE/MLE/SIGKILL detection)
        cpu_time = None
        max_mem_kb = None
        is_tle = False  # set True if prlimit sent SIGKILL (signal 9)
        if os.path.exists(profiles_file):
            try:
                raw = open(profiles_file, "r").read()
                # prlimit SIGKILL leaves a "Command terminated by signal 9" prefix line
                if raw.startswith("Command terminated by signal 9"):
                    is_tle = True
                    raw = raw.split("\n", 1)[1]  # strip prefix, parse rest
                elif raw.startswith("Command exited with non-zero status"):
                    raw = raw.split("\n", 1)[1]  # strip prefix, parse rest
                profiles = json.loads(raw.strip())
                cpu_time = float(profiles.get("user_cpu_seconds", 0)) + float(profiles.get("system_cpu_seconds", 0))
                max_mem_kb = int(profiles.get("max_resident_set_size_kbytes", 0))
            except Exception:
                pass

        if exit_code != 0 or is_tle:
            # TLE detection matching TTT exactly:
            # - CPU time: checked strictly (no tolerance) — matches AtCoder's CPU limit
            # - Elapsed time: checked with tolerance (accounts for I/O/container overhead)
            # - is_tle: prlimit sent SIGKILL (signal 9), always TLE regardless of timing
            if is_tle:
                return {"case_idx": case_idx, "score": 0, "judge": "TLE",
                        "msg": f"CPU limit exceeded (prlimit SIGKILL)", "time": elapsed}
            if elapsed > time_limit + TIME_LIMIT_TOLERANCE:
                return {"case_idx": case_idx, "score": 0, "judge": "TLE",
                        "msg": f"Wall time {elapsed:.2f}s > {time_limit + TIME_LIMIT_TOLERANCE:.1f}s", "time": elapsed}
            if cpu_time is not None and cpu_time > time_limit:  # strict, no tolerance
                return {"case_idx": case_idx, "score": 0, "judge": "TLE",
                        "msg": f"CPU time {cpu_time:.2f}s > {time_limit:.2f}s", "time": elapsed}
            # Check MLE
            if max_mem_kb is not None and max_mem_kb > 1024 * 1024:  # 1 GiB
                return {"case_idx": case_idx, "score": 0, "judge": "MLE",
                        "msg": f"Memory {max_mem_kb}KB", "time": elapsed}
            return {"case_idx": case_idx, "score": 0, "judge": "RE",
                    "msg": f"Runtime error exit={exit_code}", "time": elapsed}

        # --- Run tester ---
        try:
            judge = subprocess.run(
                [tester_path, input_file, output_file],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {"case_idx": case_idx, "score": 0, "judge": "IE",
                    "msg": "Tester timeout", "time": elapsed}

        stderr = judge.stderr.strip()

        if judge.returncode != 0:
            return {"case_idx": case_idx, "score": 0, "judge": "WA",
                    "msg": f"Tester nonzero: {stderr[:200]}", "time": elapsed}

        if "wrong answer:" in stderr.lower():
            return {"case_idx": case_idx, "score": 0, "judge": "WA",
                    "msg": f"WA: {stderr[:200]}", "time": elapsed}

        lines = stderr.splitlines()
        if not lines:
            return {"case_idx": case_idx, "score": 0, "judge": "WA",
                    "msg": "Empty tester stderr", "time": elapsed}

        m = re.match(r"Score = (\d+)", lines[-1])
        if m is None:
            return {"case_idx": case_idx, "score": 0, "judge": "WA",
                    "msg": f"Cannot parse: {lines[-1][:100]}", "time": elapsed}

        return {"case_idx": case_idx, "score": int(m.group(1)), "judge": "AC",
                "msg": "", "time": elapsed}

    except Exception as e:
        return {"case_idx": case_idx, "score": 0, "judge": "IE",
                "msg": f"{type(e).__name__}: {e}", "time": 0.0}
    finally:
        shutil.rmtree(local_tmp, ignore_errors=True)


def main():
    if len(sys.argv) != 7:
        print(json.dumps({"error": f"Usage: {sys.argv[0]} <cpp_file> <input_dir> <tester_bin> <num_cases> <num_workers> <time_limit>"}))
        sys.exit(1)

    cpp_file = sys.argv[1]
    input_dir = sys.argv[2]
    tester_bin = sys.argv[3]
    num_cases = int(sys.argv[4])
    num_workers = int(sys.argv[5])
    time_limit = float(sys.argv[6])

    overall_start = time.time()

    # --- Step 1: Compile ---
    compile_dir = tempfile.mkdtemp(prefix="ahc_compile_")
    binary_path = os.path.join(compile_dir, "a.out")

    try:
        compile_result = subprocess.run(
            [
                "g++-12", "-std=gnu++20", "-O2",
                "-DONLINE_JUDGE", "-DATCODER",
                "-Wall", "-Wextra",
                "-mtune=native", "-march=native",
                "-fconstexpr-depth=2147483647",
                "-fconstexpr-loop-limit=2147483647",
                "-fconstexpr-ops-limit=2147483647",
                "-I/opt/ac-library",
                "-I/opt/boost/gcc/include",
                "-L/opt/boost/gcc/lib",
                "-o", binary_path, cpp_file,
                "-lgmpxx", "-lgmp",
                "-I/usr/include/eigen3",
            ],
            capture_output=True, text=True, timeout=60,  # matches TTT COMPILE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "Compilation timed out"}))
        sys.exit(0)

    if compile_result.returncode != 0 or not os.path.exists(binary_path):
        err = compile_result.stderr[:500] if compile_result.stderr else "Unknown"
        print(json.dumps({"error": f"Compilation failed: {err}"}))
        sys.exit(0)

    os.chmod(binary_path, 0o755)

    # --- Step 2: Verify inputs exist ---
    # Detect input file naming pattern
    problem_id = None
    for candidate in os.listdir(input_dir):
        if candidate.endswith("_input.txt"):
            # e.g., ahc039_000000_input.txt -> problem_id = ahc039
            parts = candidate.rsplit("_", 2)
            if len(parts) >= 3:
                problem_id = parts[0]
                break

    if problem_id is None:
        print(json.dumps({"error": f"Cannot detect problem_id from files in {input_dir}"}))
        sys.exit(0)

    input_files = []
    for idx in range(num_cases):
        f = os.path.join(input_dir, f"{problem_id}_{idx:06d}_input.txt")
        if not os.path.exists(f):
            print(json.dumps({"error": f"Missing input: {f}"}))
            sys.exit(0)
        input_files.append(f)

    # --- Step 3: Run all cases in parallel ---
    task_args = [
        (binary_path, idx, input_files[idx], tester_bin, time_limit)
        for idx in range(num_cases)
    ]

    results = []
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(run_single_case, a): a[1] for a in task_args}
        for future in as_completed(futures):
            results.append(future.result())

    # Sort by case_idx for deterministic output
    results.sort(key=lambda r: r["case_idx"])

    elapsed_total = time.time() - overall_start

    # --- Step 4: Output JSON ---
    print(json.dumps({
        "case_results": results,
        "compile_time": 0.0,  # not tracked separately
        "total_time": elapsed_total,
    }))

    # Cleanup
    shutil.rmtree(compile_dir, ignore_errors=True)


if __name__ == "__main__":
    main()