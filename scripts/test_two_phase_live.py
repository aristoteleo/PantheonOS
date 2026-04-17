#!/usr/bin/env python3
"""Live integration test: long paper + long code file writing.

Verifies that after removing size guards, the LLM can write large files
directly without truncation (root cause fixed by max_tokens auto-detection).

Requires: OPENAI_API_KEY

Usage:
    OPENAI_API_KEY=sk-... python scripts/test_two_phase_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run_scenario(name, task, make_checks, model="openai/gpt-4.1-mini"):
    from pantheon.agent import Agent
    from pantheon.toolsets.file import FileManagerToolSet

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"\n{'─' * 70}")
        print(f"  Scenario: {name}")
        print(f"  Model: {model}")
        print(f"{'─' * 70}")

        fm = FileManagerToolSet("file_manager", tmpdir)
        agent = Agent(
            name="writer",
            model=model,
            instructions=(
                "You are a skilled developer and writer. "
                "Use file tools (write_file, update_file, read_file) to complete tasks. "
                "Write complete, production-quality content — do NOT leave stubs or placeholders."
            ),
        )
        await agent.toolset(fm)

        calls = []
        rejections = 0

        async def log(msg):
            nonlocal rejections
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls", []) or []:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "?").replace("file_manager__", "")
                    args_len = len(fn.get("arguments", ""))
                    calls.append(tool_name)
                    print(f"    {tool_name} ({args_len:,} chars)")
            elif msg.get("role") == "tool":
                c = str(msg.get("content", ""))
                if "content_too_large" in c:
                    rejections += 1
                    print(f"    -> REJECTED")

        resp = await agent.run(
            [{"role": "user", "content": task}],
            process_step_message=log,
            use_memory=False,
        )

        # Build and run checks
        checks = make_checks(tmpdir)
        print()
        all_pass = True
        for check_name, check_fn in checks:
            try:
                result = check_fn(tmpdir, calls, rejections)
                status = "PASS" if result else "FAIL"
                if not result:
                    all_pass = False
            except Exception as e:
                status = f"FAIL ({e})"
                all_pass = False
            print(f"    [{status}] {check_name}")

        # Show file sizes
        for f in Path(tmpdir).rglob("*"):
            if f.is_file():
                content = f.read_text(errors="replace")
                print(f"\n    {f.name}: {len(content):,} chars, {len(content.splitlines())} lines")

        print(f"\n    Tool calls: {len(calls)} total, {rejections} rejected")
        print(f"    Sequence: {' -> '.join(calls[:15])}{'...' if len(calls) > 15 else ''}")
        return all_pass


async def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("SKIP: OPENAI_API_KEY not set")
        sys.exit(0)

    print("=" * 70)
    print("  Live File Writing Test (no size guards)")
    print("=" * 70)

    results = []

    # ── Scenario 1: Long LaTeX paper ──
    paper_task = (
        "Write a complete LaTeX review paper to 'review.tex' about "
        "single-cell RNA sequencing analysis methods. Requirements:\n"
        "- \\documentclass{article} with proper packages\n"
        "- Abstract (100+ words)\n"
        "- Introduction (200+ words)\n"
        "- Methods section covering: quality control, normalization, "
        "dimensionality reduction, clustering, differential expression (300+ words total)\n"
        "- Results (150+ words)\n"
        "- Discussion (150+ words)\n"
        "- Bibliography with at least 10 \\bibitem references\n"
        "Write EVERYTHING in a single write_file call to review.tex."
    )

    def make_paper_checks(tmpdir):
        p = Path(tmpdir) / "review.tex"
        def r(): return p.read_text() if p.exists() else ""
        return [
            ("File created", lambda *_: p.exists()),
            ("File > 5000 chars", lambda *_: len(r()) > 5000),
            ("Has \\documentclass", lambda *_: "\\documentclass" in r()),
            ("Has Introduction", lambda *_: "Introduction" in r()),
            ("Has Methods", lambda *_: "Methods" in r()),
            ("Has Discussion", lambda *_: "Discussion" in r()),
            ("Has 10+ bibitem", lambda *_: r().count("\\bibitem") >= 10),
            ("No rejections", lambda tmpdir, calls, rej: rej == 0),
        ]

    r = await run_scenario("Long LaTeX Paper (single write_file)", paper_task, make_paper_checks)
    results.append(("Paper", r))

    # ── Scenario 2: Long Python code ──
    code_task = (
        "Write a complete Python file 'data_pipeline.py' that implements:\n"
        "1. A DataLoader class with methods: load_csv, load_json, load_parquet, validate_schema "
        "(each with full implementation using pandas, proper docstrings, type hints, error handling)\n"
        "2. A DataTransformer class with methods: normalize, filter_outliers, "
        "encode_categorical, impute_missing (each fully implemented)\n"
        "3. A DataExporter class with methods: to_csv, to_json, to_parquet, to_sql "
        "(each fully implemented)\n"
        "4. A Pipeline class that chains DataLoader -> DataTransformer -> DataExporter "
        "with a run() method, logging, and error handling\n"
        "5. A if __name__ == '__main__' block with example usage\n"
        "Write EVERYTHING in a single write_file call. Every method must have "
        "a real implementation (no pass, no TODO, no placeholders)."
    )

    def make_code_checks(tmpdir):
        p = Path(tmpdir) / "data_pipeline.py"
        def r(): return p.read_text() if p.exists() else ""
        return [
            ("File created", lambda *_: p.exists()),
            ("File > 3000 chars", lambda *_: len(r()) > 3000),
            ("Has DataLoader", lambda *_: "class DataLoader" in r()),
            ("Has DataTransformer", lambda *_: "class DataTransformer" in r()),
            ("Has DataExporter", lambda *_: "class DataExporter" in r()),
            ("Has Pipeline", lambda *_: "class Pipeline" in r()),
            ("Has __main__", lambda *_: "__main__" in r()),
            ("No rejections", lambda tmpdir, calls, rej: rej == 0),
        ]

    r = await run_scenario("Long Python Code (single write_file)", code_task, make_code_checks)
    results.append(("Code", r))

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print("  Summary")
    print(f"{'=' * 70}")
    for name, passed in results:
        print(f"  {name}: {'PASS' if passed else 'FAIL'}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
