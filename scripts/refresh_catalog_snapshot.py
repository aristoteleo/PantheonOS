#!/usr/bin/env python3
"""Refresh the bundled OpenRouter catalog snapshot (``pantheon/data/openrouter_catalog.json``).

The snapshot is the last-resort layer behind the live fetch and the on-disk cache: a
brand-new sandbox with no working egress still shows a real model list instead of the
BYOK-shaped 4-model list the picker used to fall back to. Run it when cutting an agent
image (or on a schedule) so that floor doesn't drift too far from what OpenRouter serves.

    python scripts/refresh_catalog_snapshot.py

Only the fields ``openrouter_catalog._derive()`` actually reads are kept — the raw
payload is ~580KB, mostly per-model ``description`` prose we never look at.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

MODELS_URL = "https://openrouter.ai/api/v1/models"
OUT = Path(__file__).resolve().parent.parent / "pantheon" / "data" / "openrouter_catalog.json"


def _trim(entry: dict) -> dict | None:
    """One /models entry -> the minimal shape _derive() consumes."""
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    arch = entry.get("architecture") or {}
    pricing = entry.get("pricing") or {}
    top = entry.get("top_provider") or {}
    reasoning = entry.get("reasoning") or {}
    return {
        "id": entry["id"],
        "name": entry.get("name"),
        "created": entry.get("created"),
        "context_length": entry.get("context_length"),
        "architecture": {"input_modalities": arch.get("input_modalities") or []},
        "supported_parameters": entry.get("supported_parameters") or [],
        "pricing": {"prompt": pricing.get("prompt"), "completion": pricing.get("completion")},
        "top_provider": {
            "context_length": top.get("context_length"),
            "max_completion_tokens": top.get("max_completion_tokens"),
        },
        "reasoning": {
            "supported_efforts": reasoning.get("supported_efforts"),
            "mandatory": reasoning.get("mandatory"),
            "default_effort": reasoning.get("default_effort"),
        },
    }


def main() -> int:
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        resp = client.get(MODELS_URL, headers={"Accept": "application/json"})
    resp.raise_for_status()
    entries = [t for t in (_trim(e) for e in resp.json().get("data", [])) if t]
    if not entries:
        print("refusing to write an empty snapshot", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # `_snapshot_generated` is informational — _parse() only reads `data`.
    payload = {
        "_snapshot_generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": entries,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    print(f"wrote {len(entries)} models -> {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
