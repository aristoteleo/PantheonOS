"""Chat export/import for portable replay bundles.

A bundle is a self-contained directory (optionally tar.gz compressed):
    <bundle>/
        manifest.json      # metadata
        chat.jsonl          # messages with paths rewritten to ./files/…
        chat.meta.json      # chat metadata (name, extra_data, …)
        files/              # referenced files, preserving directory structure
"""

import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# Path scanning helpers
# ---------------------------------------------------------------------------

# Match absolute paths that look like real files (not URLs, not bare dirs).
_ABS_PATH_RE = re.compile(
    r'(?<=["\s,:\[({])'          # preceded by delimiter (loose)
    r'(/(?:Users|home|tmp|var|opt)'  # common Unix roots
    r'/[^\s"\'\\,\]})]{3,})'     # at least 3 chars of path body
)


def _scan_file_paths(text: str) -> Set[str]:
    """Return the set of absolute file paths referenced in *text* that
    actually exist on disk (files only, not dirs)."""
    paths: Set[str] = set()
    for m in _ABS_PATH_RE.finditer(text):
        raw = m.group(1)
        # Strip trailing punctuation / markdown artifacts
        cleaned = raw.rstrip("'\"`,;:)]}*\\")
        if os.path.isfile(cleaned):
            paths.add(cleaned)
    return paths


def _relative_files_path(abs_path: str) -> str:
    """Convert an absolute path to a bundle-relative ``files/…`` path."""
    # Strip the leading slash so it nests under files/
    return "files/" + abs_path.lstrip("/")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_chat_bundle(
    memory_dir: str | Path,
    chat_id: str,
    output_dir: str | Path,
    *,
    compress: bool = False,
    size_limit_mb: float = 100,
) -> dict:
    """Export a chat and its referenced files into a portable bundle.

    Parameters
    ----------
    memory_dir : path
        Directory containing ``<chat_id>.jsonl`` / ``<chat_id>.meta.json``.
    chat_id : str
        The chat to export.
    output_dir : path
        Where to write the bundle.  Created if it doesn't exist.
    compress : bool
        If *True*, produce a ``.tar.gz`` alongside the directory.
    size_limit_mb : float
        Skip individual files larger than this (default 100 MB).

    Returns
    -------
    dict  with keys ``success``, ``bundle_path``, ``message``, ``stats``.
    """
    memory_dir = Path(memory_dir)
    output_dir = Path(output_dir)

    jsonl_path = memory_dir / f"{chat_id}.jsonl"
    meta_path = memory_dir / f"{chat_id}.meta.json"

    if not jsonl_path.exists():
        return {"success": False, "message": f"Chat {chat_id} not found"}

    # ---- read raw data ----
    jsonl_text = jsonl_path.read_text(encoding="utf-8")
    meta_text = meta_path.read_text(encoding="utf-8") if meta_path.exists() else "{}"
    meta = json.loads(meta_text)

    # ---- scan for file references ----
    all_paths = _scan_file_paths(jsonl_text) | _scan_file_paths(meta_text)
    logger.info(f"[export] Found {len(all_paths)} file references in chat {chat_id}")

    # ---- prepare output ----
    output_dir.mkdir(parents=True, exist_ok=True)
    files_dir = output_dir / "files"
    files_dir.mkdir(exist_ok=True)

    copied_files: list[dict] = []
    skipped_files: list[str] = []
    limit_bytes = int(size_limit_mb * 1024 * 1024)

    for abs_path in sorted(all_paths):
        file_size = os.path.getsize(abs_path)
        rel = _relative_files_path(abs_path)
        if file_size > limit_bytes:
            skipped_files.append(abs_path)
            logger.info(f"[export] Skipping large file ({file_size/1e6:.1f}MB): {abs_path}")
            continue
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_path, dest)
        copied_files.append({
            "original": abs_path,
            "local": rel,
            "size": file_size,
        })

    # ---- rewrite paths in jsonl/meta ----
    rewritten_jsonl = jsonl_text
    rewritten_meta = meta_text
    for f in copied_files:
        rewritten_jsonl = rewritten_jsonl.replace(f["original"], "./" + f["local"])
        rewritten_meta = rewritten_meta.replace(f["original"], "./" + f["local"])

    (output_dir / "chat.jsonl").write_text(rewritten_jsonl, encoding="utf-8")
    (output_dir / "chat.meta.json").write_text(rewritten_meta, encoding="utf-8")

    # ---- write manifest ----
    manifest = {
        "version": "1.0",
        "chat_id": chat_id,
        "chat_name": meta.get("name", ""),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "files": copied_files,
        "skipped_large_files": skipped_files,
        "stats": {
            "messages": rewritten_jsonl.count("\n"),
            "files_copied": len(copied_files),
            "files_skipped": len(skipped_files),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    bundle_path = str(output_dir)

    # ---- optional compression ----
    if compress:
        archive = str(output_dir) + ".tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(str(output_dir), arcname=output_dir.name)
        bundle_path = archive

    logger.info(
        f"[export] Bundle ready: {bundle_path} "
        f"({len(copied_files)} files, {len(skipped_files)} skipped)"
    )
    return {
        "success": True,
        "bundle_path": bundle_path,
        "message": f"Exported {len(copied_files)} files",
        "stats": manifest["stats"],
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_chat_bundle(
    memory_dir: str | Path,
    bundle_path: str | Path,
    target_root: str | Path,
) -> dict:
    """Import a chat bundle, mapping relative paths back to *target_root*.

    Parameters
    ----------
    memory_dir : path
        Destination ``memory/`` directory (e.g. ``.pantheon/memory``).
    bundle_path : path
        A bundle directory or ``.tar.gz`` file.
    target_root : path
        Workspace root on the importing machine – relative file paths are
        re-expanded under this root.

    Returns
    -------
    dict  with ``success``, ``chat_id``, ``chat_name``, ``message``.
    """
    memory_dir = Path(memory_dir)
    bundle_path = Path(bundle_path)
    target_root = Path(target_root)

    # ---- handle tar.gz ----
    tmp_dir: Optional[str] = None
    if bundle_path.suffix == ".gz" or str(bundle_path).endswith(".tar.gz"):
        tmp_dir = tempfile.mkdtemp(prefix="pantheon_import_")
        with tarfile.open(str(bundle_path), "r:gz") as tar:
            tar.extractall(tmp_dir)
        # Find the inner directory
        children = list(Path(tmp_dir).iterdir())
        if len(children) == 1 and children[0].is_dir():
            bundle_path = children[0]
        else:
            bundle_path = Path(tmp_dir)

    manifest_path = bundle_path / "manifest.json"
    jsonl_path = bundle_path / "chat.jsonl"
    meta_path = bundle_path / "chat.meta.json"

    if not jsonl_path.exists():
        return {"success": False, "message": "Invalid bundle: chat.jsonl not found"}

    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )

    jsonl_text = jsonl_path.read_text(encoding="utf-8")
    meta_text = meta_path.read_text(encoding="utf-8") if meta_path.exists() else "{}"

    # ---- copy files and rewrite paths ----
    files_dir = bundle_path / "files"
    files_copied = 0

    if files_dir.exists():
        for root, _, filenames in os.walk(files_dir):
            for fname in filenames:
                src = Path(root) / fname
                rel_to_files = src.relative_to(files_dir)
                # The relative path under files/ mirrors the original absolute path
                # (with leading slash stripped).  Restore it.
                dest = Path("/") / rel_to_files
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy2(src, dest)
                    files_copied += 1
                # Rewrite in text
                bundle_rel = f"./files/{rel_to_files.as_posix()}"
                abs_str = str(dest)
                jsonl_text = jsonl_text.replace(bundle_rel, abs_str)
                meta_text = meta_text.replace(bundle_rel, abs_str)

    # ---- write chat memory ----
    new_id = str(uuid.uuid4())
    meta = json.loads(meta_text)
    original_name = meta.get("name", manifest.get("chat_name", "Imported Chat"))
    meta["id"] = new_id

    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / f"{new_id}.jsonl").write_text(jsonl_text, encoding="utf-8")
    (memory_dir / f"{new_id}.meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Cleanup temp dir
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    logger.info(f"[import] Chat imported as {new_id} ({original_name}), {files_copied} files restored")
    return {
        "success": True,
        "chat_id": new_id,
        "chat_name": original_name,
        "message": f"Imported '{original_name}' with {files_copied} files",
    }
