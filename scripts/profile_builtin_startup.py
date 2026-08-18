#!/usr/bin/env python
"""Profile Endpoint builtin ToolSet startup timings.

This script is intentionally separate from the production startup path. It
constructs an Endpoint locally, wraps the instance's ToolSetManager startup
method, and reports per-service timing for class lookup, construction, setup,
and total startup.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from pantheon.endpoint.core import Endpoint
from pantheon.endpoint.toolsets import ToolSetMode


DEFAULT_BUILTINS = [
    "file_manager",
    "package",
    "web",
    "python_interpreter",
    "shell",
    "integrated_notebook",
    "evolution",
    "desktop",
]


def _service_name(service_config: str | dict[str, Any]) -> str:
    if isinstance(service_config, str):
        return service_config
    return service_config.get("name") or service_config.get("type") or str(service_config)


async def profile_builtin_startup(
    builtins: list[str],
    workspace: Path,
    iterations: int,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []

    for iteration in range(iterations):
        endpoint = Endpoint(
            config={
                "builtin_services": builtins,
                "service_modes": {"default": "local"},
                "service_name": "builtin-startup-profiler",
                "workspace_path": str(workspace / f"run-{iteration}"),
                "id_hash": f"profile_{uuid.uuid4().hex[:8]}",
                "allow_file_transfer": False,
            }
        )
        manager = endpoint.toolset_manager
        records: list[dict[str, Any]] = []

        async def profiled_start_toolset_unified(
            service_config: str | dict[str, Any],
            mode: str,
            retries: int = 3,
        ) -> bool:
            service_type, params = manager._parse_service_config(service_config)
            service_name = params.get("name", service_type)
            record: dict[str, Any] = {
                "service": service_name,
                "service_type": service_type,
                "mode": mode,
                "success": False,
            }
            records.append(record)
            total_start = time.perf_counter()

            try:
                prepare_start = time.perf_counter()
                toolset_args = manager._prepare_toolset_args(service_type, params)
                record["prepare_s"] = time.perf_counter() - prepare_start

                if mode == "local":
                    lookup_start = time.perf_counter()
                    toolset_class = manager._get_toolset_class(service_type)
                    record["class_lookup_s"] = time.perf_counter() - lookup_start

                    construct_start = time.perf_counter()
                    toolset_instance = toolset_class(**toolset_args)
                    record["construct_s"] = time.perf_counter() - construct_start

                    setup_start = time.perf_counter()
                    await toolset_instance.run_setup()
                    record["run_setup_s"] = time.perf_counter() - setup_start

                    service_id = f"local_{service_name}_{uuid.uuid4().hex[:8]}"
                    manager.local_toolsets[service_id] = toolset_instance
                    manager.services[service_id] = {
                        "id": service_id,
                        "name": service_name,
                        "mode": ToolSetMode.LOCAL,
                        "instance": toolset_instance,
                    }
                    record["service_id"] = service_id
                    record["success"] = True
                    return True

                # Remote mode is uncommon in the current default profile, but keep
                # the wrapper honest if the caller passes a custom config later.
                remote_start = time.perf_counter()
                result = await original_start_toolset_unified(
                    service_config,
                    mode,
                    retries,
                )
                record["remote_original_s"] = time.perf_counter() - remote_start
                record["success"] = bool(result)
                return bool(result)
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                return False
            finally:
                record["total_s"] = time.perf_counter() - total_start

        original_start_toolset_unified = manager._start_toolset_unified
        manager._start_toolset_unified = profiled_start_toolset_unified

        start = time.perf_counter()
        result = await manager.start_services(builtins, local_retries=10, remote_retries=10)
        total_s = time.perf_counter() - start
        ready_start = time.perf_counter()
        services_ready = await endpoint.services_ready()
        ready_check_s = time.perf_counter() - ready_start

        try:
            await manager.cleanup()
        except Exception as exc:
            result.setdefault("cleanup_errors", []).append(str(exc))

        runs.append(
            {
                "iteration": iteration,
                "builtins": builtins,
                "result": result,
                "total_start_services_s": total_s,
                "services_ready": services_ready,
                "services_ready_check_s": ready_check_s,
                "records": sorted(records, key=lambda item: item["service"]),
            }
        )

    return {"iterations": iterations, "runs": runs}


async def profile_endpoint_run_setup(builtins: list[str], workspace: Path) -> dict[str, Any]:
    endpoint = Endpoint(
        config={
            "builtin_services": builtins,
            "service_modes": {"default": "local"},
            "service_name": "endpoint-run-setup-profiler",
            "workspace_path": str(workspace / "full-endpoint"),
            "id_hash": f"profile_{uuid.uuid4().hex[:8]}",
            "allow_file_transfer": False,
        }
    )

    marks: dict[str, float] = {}
    records: list[dict[str, Any]] = []

    def mark(name: str) -> None:
        marks[name] = time.perf_counter()

    manager = endpoint.toolset_manager
    original_start_toolset_unified = manager._start_toolset_unified

    async def profiled_start_toolset_unified(
        service_config: str | dict[str, Any],
        mode: str,
        retries: int = 3,
    ) -> bool:
        service_type, params = manager._parse_service_config(service_config)
        service_name = params.get("name", service_type)
        record: dict[str, Any] = {
            "service": service_name,
            "service_type": service_type,
            "mode": mode,
            "success": False,
        }
        records.append(record)
        total_start = time.perf_counter()
        try:
            prepare_start = time.perf_counter()
            toolset_args = manager._prepare_toolset_args(service_type, params)
            record["prepare_s"] = time.perf_counter() - prepare_start

            if mode == "local":
                lookup_start = time.perf_counter()
                toolset_class = manager._get_toolset_class(service_type)
                record["class_lookup_s"] = time.perf_counter() - lookup_start

                construct_start = time.perf_counter()
                toolset_instance = toolset_class(**toolset_args)
                record["construct_s"] = time.perf_counter() - construct_start

                setup_start = time.perf_counter()
                await toolset_instance.run_setup()
                record["run_setup_s"] = time.perf_counter() - setup_start

                service_id = f"local_{service_name}_{uuid.uuid4().hex[:8]}"
                manager.local_toolsets[service_id] = toolset_instance
                manager.services[service_id] = {
                    "id": service_id,
                    "name": service_name,
                    "mode": ToolSetMode.LOCAL,
                    "instance": toolset_instance,
                }
                record["service_id"] = service_id
                record["success"] = True
                return True

            result = await original_start_toolset_unified(service_config, mode, retries)
            record["success"] = bool(result)
            return bool(result)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            return False
        finally:
            record["total_s"] = time.perf_counter() - total_start

    manager._start_toolset_unified = profiled_start_toolset_unified

    mark("start")
    try:
        mark("phase1_load_config_start")
        from pantheon.settings import get_settings

        mcp_config = get_settings().get_mcp_config()
        mcp_result = await endpoint.mcp_manager.load_config(mcp_config)
        mark("phase1_load_config_done")

        os.environ["ENDPOINT_MCP_URI"] = endpoint.mcp_manager.get_unified_uri()
        mark("phase1_done")

        mark("phase2_builtin_start")
        builtin_result = await endpoint.toolset_manager.start_services(
            builtins,
            local_retries=10,
            remote_retries=10,
        )
        mark("phase2_builtin_done")

        mark("services_ready_loop_start")
        loop_count = 0
        while True:
            loop_count += 1
            if await endpoint.services_ready():
                break
            await asyncio.sleep(1)
        mark("services_ready_loop_done")

        return {
            "builtins": builtins,
            "mcp_result": mcp_result,
            "builtin_result": builtin_result,
            "services_ready_loop_count": loop_count,
            "marks": marks,
            "durations": {
                "phase1_load_config_s": marks["phase1_load_config_done"]
                - marks["phase1_load_config_start"],
                "phase1_total_s": marks["phase1_done"]
                - marks["phase1_load_config_start"],
                "phase2_builtin_s": marks["phase2_builtin_done"]
                - marks["phase2_builtin_start"],
                "services_ready_loop_s": marks["services_ready_loop_done"]
                - marks["services_ready_loop_start"],
                "total_profiled_run_setup_s": marks["services_ready_loop_done"]
                - marks["start"],
            },
            "records": sorted(records, key=lambda item: item["service"]),
        }
    finally:
        try:
            await manager.cleanup()
        except Exception:
            pass


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    service_totals: dict[str, list[float]] = {}
    phase_totals: dict[str, list[float]] = {}
    run_totals: list[float] = []

    for run in report["runs"]:
        run_totals.append(run["total_start_services_s"])
        for record in run["records"]:
            service_totals.setdefault(record["service"], []).append(record["total_s"])
            for phase in ("prepare_s", "class_lookup_s", "construct_s", "run_setup_s"):
                if phase in record:
                    phase_totals.setdefault(f"{record['service']}:{phase}", []).append(
                        record[phase]
                    )

    def stats(values: list[float]) -> dict[str, float]:
        values = sorted(values)
        return {
            "min": values[0],
            "mean": sum(values) / len(values),
            "max": values[-1],
        }

    return {
        "total_start_services_s": stats(run_totals),
        "services": {name: stats(values) for name, values in sorted(service_totals.items())},
        "phases": {name: stats(values) for name, values in sorted(phase_totals.items())},
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--builtins",
        default=",".join(DEFAULT_BUILTINS),
        help="Comma-separated builtin toolsets to start.",
    )
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--full-endpoint",
        action="store_true",
        help="Profile the full Endpoint.run_setup phase sequence.",
    )
    parser.add_argument(
        "--workspace",
        default=".tmp/builtin-startup-profile",
        help="Workspace root for profiler runs.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    args = parser.parse_args()

    builtins = [item.strip() for item in args.builtins.split(",") if item.strip()]
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    # Avoid a local profile accidentally paying LLM warmup if run_setup is added
    # later; the current script profiles ToolSetManager directly.
    os.environ.setdefault("LLM_WARMUP_MODEL", "")

    if args.full_endpoint:
        report = await profile_endpoint_run_setup(builtins, workspace)
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    report = await profile_builtin_startup(builtins, workspace, args.iterations)
    report["summary"] = summarize(report)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
