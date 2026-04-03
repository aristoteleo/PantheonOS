"""Shared utilities for detecting newly created image files via filesystem snapshots."""

import base64
from pathlib import Path

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# Standard directory (relative to workspace root) where agents should save
# generated images so claw channels can detect and forward them.
IMAGE_OUTPUT_DIR = ".pantheon/images"


def snapshot_images(workdir: str | Path) -> dict[str, float]:
    """Return ``{path: mtime}`` for image files in the top-level of *workdir*."""
    scan_dir = Path(workdir)
    snapshot: dict[str, float] = {}
    try:
        for p in scan_dir.iterdir():
            if p.suffix.lower() in _IMAGE_EXTENSIONS and p.is_file():
                snapshot[str(p)] = p.stat().st_mtime
    except OSError:
        pass
    return snapshot


def diff_snapshots(
    pre: dict[str, float], post: dict[str, float]
) -> list[str]:
    """Return file paths that are new or modified between *pre* and *post*."""
    return [
        path
        for path, mtime in post.items()
        if path not in pre or mtime > pre[path]
    ]


def encode_images_to_uris(paths: list[str]) -> list[str]:
    """Base64-encode image files and return data-URI strings."""
    uris: list[str] = []
    for path in paths:
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            ext = Path(path).suffix.lower().lstrip(".")
            mime = "jpeg" if ext in ("jpg", "jpeg") else "png"
            uris.append(f"data:image/{mime};base64,{b64}")
        except OSError:
            continue
    return uris
