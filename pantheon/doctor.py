"""
pantheon/doctor.py — Environment diagnostics command.

Run with: pantheon doctor

Surfaces common environment misconfigurations before execution,
covering contracts already declared in package metadata and
the current startup setup check.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import List, Optional


# ── Result model ────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    status: str          # "PASS" | "WARN" | "FAIL"
    message: str
    fix_hints: List[str] = field(default_factory=list)


# ── Individual checks ────────────────────────────────────────────────────────


def _check_python_version() -> CheckResult:
    """Python version satisfies requires-python >= 3.10."""
    major, minor = sys.version_info.major, sys.version_info.minor
    version_str = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= (3, 10):
        return CheckResult(
            "Python version", "PASS",
            f"Python {version_str}  (requires >=3.10)",
        )
    return CheckResult(
        "Python version", "FAIL",
        f"Python {version_str} is below the required >=3.10",
        fix_hints=["Install Python 3.10+: https://python.org/downloads"],
    )


def _check_api_keys() -> CheckResult:
    """Mirror check_and_run_setup() exactly, in the same order.

    Replicates the four-step detection from check_and_run_setup() so that
    doctor and the startup wizard always agree on whether the environment
    is configured:
      1. SKIP_SETUP_WIZARD env var
      2. Standard PROVIDER_API_KEYS
      3. Custom endpoint keys (CUSTOM_ENDPOINT_ENVS)
      4. Legacy LLM_API_KEY
    """
    from pantheon.utils.model_selector import CUSTOM_ENDPOINT_ENVS, PROVIDER_API_KEYS

    # 1. Explicit skip flag
    if os.environ.get("SKIP_SETUP_WIZARD", "").lower() in ("1", "true", "yes"):
        return CheckResult("LLM API key", "PASS", "SKIP_SETUP_WIZARD is set")

    # 2. Standard provider keys
    configured = [
        name for name, env_var in PROVIDER_API_KEYS.items()
        if os.environ.get(env_var, "")
    ]
    if configured:
        return CheckResult(
            "LLM API key", "PASS",
            f"Provider key configured: {', '.join(configured)}",
        )

    # 3. Custom endpoint keys
    custom_configured = [
        cfg.display_name for cfg in CUSTOM_ENDPOINT_ENVS.values()
        if os.environ.get(cfg.api_key_env, "")
    ]
    if custom_configured:
        return CheckResult(
            "LLM API key", "PASS",
            f"Custom endpoint key configured: {', '.join(custom_configured)}",
        )

    # 4. Legacy LLM_API_KEY
    if os.environ.get("LLM_API_KEY", ""):
        return CheckResult("LLM API key", "PASS", "LLM_API_KEY is set")

    return CheckResult(
        "LLM API key", "WARN",
        "No LLM provider key found — Pantheon will prompt the setup wizard on start",
        fix_hints=["Run: pantheon setup"],
    )


def _check_core_imports() -> CheckResult:
    """Core notebook packages are importable (declared as required deps)."""
    missing = []
    for pkg in ("ipykernel", "jupyter_client", "nbformat"):
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg.replace("_", "-"))
    if not missing:
        return CheckResult(
            "Core packages", "PASS",
            "ipykernel, jupyter-client, nbformat are importable",
        )
    return CheckResult(
        "Core packages", "FAIL",
        f"Required packages not importable: {', '.join(missing)}",
        fix_hints=[f"pip install {' '.join(missing)}"],
    )


def _check_r_runtime() -> Optional[CheckResult]:
    """R runtime is available when the r extra (rpy2) is installed.

    The r optional-dependency comment in pyproject.toml explicitly states
    'requires R installed on system', so a missing R binary is a WARN.
    """
    try:
        importlib.import_module("rpy2")
    except ImportError:
        return None  # r extra not installed — skip silently

    if shutil.which("Rscript") or shutil.which("R"):
        return CheckResult("R runtime", "PASS", "R is available on PATH")
    return CheckResult(
        "R runtime", "WARN",
        "rpy2 is installed but R runtime not found on PATH",
        fix_hints=["Install R: https://www.r-project.org/"],
    )


# ── Runner ───────────────────────────────────────────────────────────────────


def _collect_checks() -> List[CheckResult]:
    results: List[CheckResult] = []
    results.append(_check_python_version())
    results.append(_check_api_keys())
    results.append(_check_core_imports())
    r_check = _check_r_runtime()
    if r_check is not None:
        results.append(r_check)
    return results


def run_doctor() -> int:
    """Run all environment checks and print a structured report.

    Returns:
        0  — all checks pass or warn only
        1  — at least one FAIL
    """
    from rich import box
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print()
    console.print(
        "[bold cyan]Pantheon Doctor[/bold cyan]  —  Environment Diagnostics"
    )
    console.print()

    results = _collect_checks()

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("status", width=6, no_wrap=True)
    table.add_column("check", style="bold", no_wrap=True)
    table.add_column("detail")

    _STATUS_STYLE = {
        "PASS": "[green]PASS[/green]",
        "WARN": "[yellow]WARN[/yellow]",
        "FAIL": "[red]FAIL[/red]",
    }

    for r in results:
        table.add_row(_STATUS_STYLE[r.status], r.name, r.message)

    console.print(table)

    # Fix hints for non-passing checks
    actionable = [r for r in results if r.status in ("WARN", "FAIL") and r.fix_hints]
    if actionable:
        console.print()
        for r in actionable:
            console.print(f"  [bold]{r.name}[/bold]")
            for hint in r.fix_hints:
                console.print(f"    {hint}", style="dim")
        console.print()

    fails = sum(1 for r in results if r.status == "FAIL")
    warns = sum(1 for r in results if r.status == "WARN")

    if fails:
        console.print(
            f"[red]✗  {fails} failure(s).[/red]  "
            "Fix the issues above before running Pantheon."
        )
        return 1
    if warns:
        console.print(
            f"[yellow]⚠  {warns} warning(s).[/yellow]  "
            "Pantheon can run; some capabilities may be unavailable."
        )
        return 0
    console.print("[green]✓  All checks passed.[/green]")
    return 0
