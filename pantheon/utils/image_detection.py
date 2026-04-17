"""Shared utilities for detecting newly created image files via filesystem snapshots."""

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# Standard directory (relative to workspace root) where agents should save
# generated images so claw channels can detect and forward them.
IMAGE_OUTPUT_DIR = ".pantheon/images"

# Default limits (can be overridden via claw config images.max_size_bytes / images.max_dimension)
DEFAULT_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_MAX_DIMENSION = 1568


def _get_image_limits() -> tuple[int, int]:
    """Read image size limits from claw config, falling back to defaults."""
    try:
        from pantheon.claw.config import ClawConfigStore
        cfg = ClawConfigStore().load()
        images_cfg = cfg.get("images", {})
        max_size = images_cfg.get("max_size_bytes", DEFAULT_MAX_SIZE_BYTES)
        max_dim = images_cfg.get("max_dimension", DEFAULT_MAX_DIMENSION)
        return int(max_size), int(max_dim)
    except Exception:
        return DEFAULT_MAX_SIZE_BYTES, DEFAULT_MAX_DIMENSION


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
    """Base64-encode image files and return data-URI strings.

    Skips files that exceed the configured ``max_size_bytes`` limit.
    """
    max_size, _max_dim = _get_image_limits()
    uris: list[str] = []
    for path in paths:
        try:
            file_size = Path(path).stat().st_size
            if file_size > max_size:
                logger.warning(
                    "Skipping image %s: size %d exceeds limit %d",
                    path, file_size, max_size,
                )
                continue
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            ext = Path(path).suffix.lower().lstrip(".")
            mime = "jpeg" if ext in ("jpg", "jpeg") else "png"
            uris.append(f"data:image/{mime};base64,{b64}")
        except OSError:
            continue
    return uris
