"""Helpers for exporting and loading Pantheon package context."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from pantheon.utils.log import logger

CONTEXT_ENV = "PANTHEON_CONTEXT"
DEFAULT_PACKAGES_SUBDIR = ".pantheon/packages"


def _default_json(value: Any):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def _normalize_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    return str(Path(path).expanduser().resolve())


def derive_packages_path(workdir: str | Path | None = None) -> str:
    """Return the canonical packages directory for a workdir."""

    if workdir:
        root = Path(workdir).expanduser().resolve()
    else:
        root = Path.cwd()
    return str((root / DEFAULT_PACKAGES_SUBDIR).resolve())


def build_context_payload(
    *,
    workdir: str | Path | None = None,
    context_variables: Mapping[str, Any] | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict:
    """Construct a normalized context payload ready for export."""
    normalized_workdir = _normalize_path(workdir)
    # remove key value with _ prefix from context_variables keys
    context_variables = dict(context_variables or {})
    payload: dict[str, Any] = {
        "workdir": normalized_workdir,
        "context_variables": {k: v for k, v in context_variables.items() if not k.startswith("_")},
    }
    
    # Auto-inject endpoint_mcp_uri from ENDPOINT_MCP_URI env var if available
    # This is a top-level field (like workdir), not part of context_variables
    endpoint_mcp_uri = os.environ.get("ENDPOINT_MCP_URI")
    if endpoint_mcp_uri:
        payload["endpoint_mcp_uri"] = endpoint_mcp_uri
    
    if extras:
        payload.update(extras)
    return payload


def build_context_env(
    *,
    workdir: str | Path | None = None,
    context_variables: Mapping[str, Any] | None = None,
    extras: Mapping[str, Any] | None = None,
    base_env: Mapping[str, Any] | None = None,
    optimize: bool = True,
    max_env_size: int = 100 * 1024,
) -> dict:
    """Return a copy of base_env with PANTHEON_CONTEXT exported.

    Args:
        workdir: Working directory path.
        context_variables: Context variables to export.
        extras: Extra fields to include in the context.
        base_env: Base environment to copy from.
        optimize: Whether to optimize environment size (default True).
        max_env_size: Maximum environment size in bytes before optimization (default 100KB).

    Returns:
        Environment dictionary with PANTHEON_CONTEXT exported.
    """

    payload = build_context_payload(
        workdir=workdir,
        context_variables=context_variables,
        extras=extras,
    )
    env: dict[str, Any] = dict(base_env or {})
    export_context(payload, env=env)

    # Auto-optimize to avoid ARG_MAX/E2BIG issues
    if optimize:
        env = optimize_context_env(env, max_size=max_env_size)

    return env


def export_context(payload: Mapping[str, Any], env: dict | None = None) -> dict:
    """Serialize payload into env (defaults to os.environ) for downstream use."""

    target = env if env is not None else os.environ
    normalized = {
        "workdir": _normalize_path(payload.get("workdir")),
        "context_variables": _filter_serializable_context(
            payload.get("context_variables") or {}
        ),
    }
    extras = {
        key: value
        for key, value in payload.items()
        if key not in ("workdir", "context_variables")
    }
    if extras:
        normalized.update(extras)

    serialized = json.dumps(normalized, default=_default_json)
    target[CONTEXT_ENV] = serialized
    return target


def load_context(default: Mapping[str, Any] | None = None) -> dict:
    """Load the serialized payload from env, returning a normalized dict."""

    baseline = {"workdir": None, "context_variables": {}, "endpoint_mcp_uri": None}
    if default:
        baseline.update(default)
        if "context_variables" in default:
            baseline["context_variables"] = dict(
                baseline.get("context_variables") or {}
            ) | dict(default["context_variables"])

    raw = os.environ.get(CONTEXT_ENV)
    if not raw:
        return baseline

    try:
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            return baseline
        baseline.update(loaded)
        return baseline
    except (json.JSONDecodeError, TypeError):
        return baseline
 

def _filter_serializable_context(values: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only entries whose values can be JSON-serialized."""

    clean: dict[str, Any] = {}
    for key, value in dict(values).items():
        normalized_key = key if isinstance(key, str) else str(key)
        if _is_json_serializable(value):
            clean[normalized_key] = value
    return clean


def _is_json_serializable(value: Any) -> bool:
    try:
        json.dumps(value, default=_default_json)
        return True
    except (TypeError, ValueError):
        return False


def optimize_context_env(
    env: dict,
    max_size: int = 100 * 1024,
) -> dict:
    """Optimize environment variables size to avoid ARG_MAX/E2BIG limits.

    Args:
        env: Original environment variables dictionary.
        max_size: Maximum allowed size in bytes (default 100KB).

    Returns:
        Optimized environment variables dictionary.
    """
    total_size = sum(len(str(k)) + len(str(v)) for k, v in env.items())

    if total_size <= max_size:
        return env  # No optimization needed

    logger.warning(
        f"Environment variables size ({total_size} bytes) exceeds "
        f"safe limit ({max_size} bytes), applying optimization"
    )

    env = env.copy()

    # Step 1: Remove known large non-essential environment variables
    REMOVABLE_VARS = ["LS_COLORS", "LESSOPEN", "LESSCLOSE"]
    for var in REMOVABLE_VARS:
        if var in env:
            removed_size = len(str(env[var]))
            env.pop(var)
            logger.debug(f"Removed {var} ({removed_size} bytes)")

    # Step 2: Prune large fields in PANTHEON_CONTEXT
    pantheon_context = env.get("PANTHEON_CONTEXT")
    if pantheon_context:
        try:
            ctx = json.loads(pantheon_context)

            if "context_variables" in ctx:
                cv = ctx["context_variables"]
                original_cv_size = len(json.dumps(cv))

                # Identify large fields (> 10KB)
                large_fields = []
                for key, value in list(cv.items()):
                    value_size = len(json.dumps(value))
                    if value_size > 10 * 1024:  # 10KB threshold
                        large_fields.append((key, value_size))

                if large_fields:
                    logger.warning(f"Found {len(large_fields)} large context variables:")
                    for key, size in large_fields:
                        logger.warning(f"  - {key}: {size} bytes")
                        # Replace with placeholder
                        cv[key] = f"<removed: too large ({size} bytes)>"

                    # Re-serialize
                    env["PANTHEON_CONTEXT"] = json.dumps(ctx)
                    new_cv_size = len(json.dumps(cv))
                    logger.info(
                        f"Reduced context_variables from {original_cv_size} to {new_cv_size} bytes"
                    )

        except Exception as e:
            logger.error(f"Failed to prune PANTHEON_CONTEXT: {e}")

    # Recalculate size
    new_total_size = sum(len(str(k)) + len(str(v)) for k, v in env.items())
    logger.info(f"Environment size after optimization: {new_total_size} bytes")

    return env


def get_env_size_info(env: dict) -> dict:
    """Get environment variables size information for diagnostics.

    Args:
        env: Environment variables dictionary.

    Returns:
        Dictionary with size information.
    """
    total_size = sum(len(str(k)) + len(str(v)) for k, v in env.items())

    # Find largest 5 environment variables
    sorted_vars = sorted(
        [(k, len(str(v))) for k, v in env.items()],
        key=lambda x: x[1],
        reverse=True
    )

    return {
        "total_size": total_size,
        "total_count": len(env),
        "largest_vars": sorted_vars[:5],
    }


__all__ = [
    "CONTEXT_ENV",
    "build_context_payload",
    "derive_packages_path",
    "export_context",
    "load_context",
    "optimize_context_env",
    "get_env_size_info",
]
