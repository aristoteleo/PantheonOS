"""Chat export/import for portable replay bundles.

A bundle is a self-contained directory (optionally tar.gz compressed):
    <bundle>/
        manifest.json      # metadata
        chat.jsonl          # messages with paths rewritten to ./files/…
        chat.meta.json      # chat metadata (name, extra_data, …)
        files/              # referenced files, preserving directory structure
"""

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional, Set
from urllib.parse import unquote, urlparse

from loguru import logger

# ---------------------------------------------------------------------------
# Path scanning helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ExportRoot:
    kind: str
    path: Path
    strip_prefixes: tuple[str, ...] = ()


_ROOT_KIND_ORDER = {
    "chat": 0,
    "workspace": 1,
    "memory": 2,
    "cwd": 3,
    "external": 99,
}

# Match absolute paths that look like real files (not URLs, not bare dirs).
_ABS_PATH_RE = re.compile(
    r'(?<![\w:/])'               # not part of a URL or another token
    r'(/[^\s"\'\\,\]})]{3,})'    # absolute path body
)

_FILE_URI_RE = re.compile(r'file://[^\s"\'\]})>]+')

_GENERIC_REL_PATH_RE = re.compile(
    r'(?<![\w./-])'
    r'((?:\.?/)?(?:[A-Za-z0-9_. -]+/)+[A-Za-z0-9_. -]+\.[A-Za-z0-9]{1,12})'
    r'(?![\w./-])'
)

_REL_PATH_RE = re.compile(
    r'(?<=["\s,:\[({])'
    r'(\.pantheon/(?:brain|tmp|latex|skills)/[^\s"\'\\,\]})]{3,})'
)

_WORKDIR_RE = re.compile(
    r'(?<=["\s,:\[({])'
    r'(workdir/[^\s"\'\\,\]})]{3,})'
)


_SKIP_PREFIXES = (
    "/usr/", "/bin/", "/sbin/", "/opt/homebrew/", "/opt/local/",
    "/System/", "/Library/", "/Applications/",
    "/nix/", "/snap/",
)

_SKIP_EXTENSIONS = {
    "", ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe",
    ".o", ".a", ".ko", ".class",
}


def _strip_path_suffix(path: str) -> str:
    return path.rstrip("'\"`,;:)]}*\\")


def _is_exportable(path: str) -> bool:
    """Check whether a file should be included in the bundle."""
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext in _SKIP_EXTENSIONS:
        return False
    if not os.access(path, os.R_OK):
        return False
    return True


def _file_uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return ""
    return unquote(parsed.path)


def _root_kind_rank(kind: str) -> int:
    return _ROOT_KIND_ORDER.get(kind, 50)


def _safe_relative_posix(path: str) -> str:
    return PurePosixPath(*_safe_posix_parts(path)).as_posix()


def _root_relative_variants(relative: str, root: _ExportRoot) -> list[str]:
    relative = relative.lstrip("./")
    result = [relative]
    for prefix in root.strip_prefixes:
        if relative.startswith(prefix):
            stripped = relative[len(prefix):].lstrip("/")
            if stripped:
                result.insert(0, stripped)

    unique: list[str] = []
    seen: set[str] = set()
    for item in result:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _root_relative_path(path: Path, root: _ExportRoot) -> str:
    relative = path.relative_to(root.path.resolve()).as_posix()
    for prefix in root.strip_prefixes:
        if relative.startswith(prefix):
            stripped = relative[len(prefix):].lstrip("/")
            if stripped:
                return _safe_relative_posix(stripped)
    return _safe_relative_posix(relative)


def _external_relative_path(abs_path: str) -> str:
    path = Path(abs_path)
    digest = hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()[:12]
    name = path.name or "file"
    return _safe_relative_posix(f"_external/{digest}/{name}")


def _add_existing_file(paths: Set[str], path: str) -> None:
    if path and os.path.isfile(path) and _is_exportable(path):
        paths.add(path)


def _add_existing_export_file(entries: dict[str, dict], path: str, root: _ExportRoot) -> None:
    if not path or not os.path.isfile(path) or not _is_exportable(path):
        return

    try:
        resolved = Path(path).resolve()
        relative = _root_relative_path(resolved, root)
    except ValueError:
        return

    key = str(resolved)
    current = entries.get(key)
    candidate = {
        "copy_path": key,
        "original": str(Path(path)),
        "relative": relative,
        "local": f"files/{relative}",
        "source_root_kind": root.kind,
    }
    if current is None or (
        _root_kind_rank(root.kind) < _root_kind_rank(current["source_root_kind"])
    ):
        entries[key] = candidate


def _add_absolute_export_file(
    entries: dict[str, dict],
    path: str,
    roots: list[_ExportRoot],
) -> None:
    if not path or not os.path.isfile(path) or not _is_exportable(path):
        return
    candidate = _resolved_export_entry(path, roots)
    key = candidate["copy_path"]
    current = entries.get(key)
    if current is None or (
        _root_kind_rank(candidate["source_root_kind"])
        < _root_kind_rank(current["source_root_kind"])
    ):
        entries[key] = candidate


def _add_workspace_relative_file(
    paths: Set[str],
    candidate: str,
    workspace_root: str,
) -> None:
    if not workspace_root:
        return
    candidate = candidate.strip()
    if not candidate:
        return

    variants = [candidate]
    parts = candidate.split()
    variants.extend(" ".join(parts[index:]) for index in range(1, len(parts)))

    for variant in variants:
        cleaned = variant.strip().lstrip("./")
        if not cleaned or cleaned.startswith(("files/", "./files/")):
            continue
        if ".." in Path(cleaned).parts:
            continue
        _add_existing_file(paths, os.path.join(workspace_root, cleaned))


def _relative_candidate_variants(candidate: str) -> list[str]:
    candidate = candidate.strip()
    if not candidate:
        return []

    variants = [candidate]
    parts = candidate.split()
    variants.extend(" ".join(parts[index:]) for index in range(1, len(parts)))

    result: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        cleaned = variant.strip().lstrip("./")
        if not cleaned or cleaned.startswith(("files/", "./files/")):
            continue
        path_parts = Path(cleaned).parts
        if ".." in path_parts:
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
        if path_parts and path_parts[0] == "workdir":
            without_workdir = Path(*path_parts[1:]).as_posix()
            if without_workdir and without_workdir not in seen:
                seen.add(without_workdir)
                result.append(without_workdir)
    return result


def _add_relative_export_file(
    entries: dict[str, dict],
    candidate: str,
    roots: list[_ExportRoot],
) -> None:
    for cleaned in _relative_candidate_variants(candidate):
        for root in roots:
            for root_relative in _root_relative_variants(cleaned, root):
                _add_existing_export_file(entries, str(root.path / root_relative), root)


def _scan_export_file_paths(text: str, roots: list[_ExportRoot]) -> dict[str, dict]:
    entries: dict[str, dict] = {}

    for m in _FILE_URI_RE.finditer(text):
        raw = _strip_path_suffix(m.group(0))
        _add_absolute_export_file(entries, _file_uri_to_path(raw), roots)

    for m in _ABS_PATH_RE.finditer(text):
        cleaned = _strip_path_suffix(m.group(1))
        _add_absolute_export_file(entries, cleaned, roots)

    for regex in (_REL_PATH_RE, _WORKDIR_RE, _GENERIC_REL_PATH_RE):
        for m in regex.finditer(text):
            _add_relative_export_file(entries, _strip_path_suffix(m.group(1)), roots)

    return entries


def _scan_export_file_paths_from_value(value: object, roots: list[_ExportRoot]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    if isinstance(value, str):
        entries.update(_scan_export_file_paths(value, roots))
        _add_relative_export_file(entries, value, roots)
    elif isinstance(value, dict):
        for child in value.values():
            entries.update(_scan_export_file_paths_from_value(child, roots))
    elif isinstance(value, list):
        for child in value:
            entries.update(_scan_export_file_paths_from_value(child, roots))
    return entries


def _scan_jsonl_export_file_paths(text: str, roots: list[_ExportRoot]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            entries.update(_scan_export_file_paths(line, roots))
            continue
        entries.update(_scan_export_file_paths_from_value(value, roots))
    return entries


def _scan_json_export_file_paths(text: str, roots: list[_ExportRoot]) -> dict[str, dict]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return _scan_export_file_paths(text, roots)
    return _scan_export_file_paths_from_value(value, roots)


def _scan_file_paths(text: str, workspace_root: str = "") -> Set[str]:
    """Return the set of file paths referenced in *text* that
    actually exist on disk (files only, not dirs)."""
    paths: Set[str] = set()

    # file:// URIs (including markdown links and image_url blocks)
    for m in _FILE_URI_RE.finditer(text):
        raw = _strip_path_suffix(m.group(0))
        _add_existing_file(paths, _file_uri_to_path(raw))

    # Absolute paths
    for m in _ABS_PATH_RE.finditer(text):
        cleaned = _strip_path_suffix(m.group(1))
        _add_existing_file(paths, cleaned)

    # Relative paths under the importing/exporting workspace.
    for regex in (_REL_PATH_RE, _WORKDIR_RE, _GENERIC_REL_PATH_RE):
        for m in regex.finditer(text):
            cleaned = _strip_path_suffix(m.group(1))
            if cleaned.startswith(("./files/", "files/")) or ".." in Path(cleaned).parts:
                continue
            _add_workspace_relative_file(paths, cleaned, workspace_root)

    return paths


def _scan_file_paths_from_value(value: object, workspace_root: str = "") -> Set[str]:
    paths: Set[str] = set()
    if isinstance(value, str):
        paths.update(_scan_file_paths(value, workspace_root))
        _add_workspace_relative_file(paths, value, workspace_root)
    elif isinstance(value, dict):
        for child in value.values():
            paths.update(_scan_file_paths_from_value(child, workspace_root))
    elif isinstance(value, list):
        for child in value:
            paths.update(_scan_file_paths_from_value(child, workspace_root))
    return paths


def _scan_jsonl_file_paths(text: str, workspace_root: str = "") -> Set[str]:
    paths: Set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            paths.update(_scan_file_paths(line, workspace_root))
            continue
        paths.update(_scan_file_paths_from_value(value, workspace_root))
    return paths


def _scan_json_file_paths(text: str, workspace_root: str = "") -> Set[str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return _scan_file_paths(text, workspace_root)
    return _scan_file_paths_from_value(value, workspace_root)


def _project_meta(meta: dict) -> dict:
    project = meta.get("extra_data", {}).get("project", {})
    if not isinstance(project, dict):
        project = meta.get("project", {})
    return project if isinstance(project, dict) else {}


def _workspace_roots(memory_dir: Path, meta: dict) -> list[_ExportRoot]:
    roots: list[_ExportRoot] = []
    project = _project_meta(meta)
    workspace_path = project.get("workspace_path") if isinstance(project, dict) else None
    if isinstance(workspace_path, str) and workspace_path:
        roots.append(
            _ExportRoot(
                "workspace",
                Path(workspace_path),
                ("workdir/",),
            )
        )

    # .pantheon/memory -> .pantheon -> workspace
    roots.append(_ExportRoot("memory", memory_dir.parent.parent, ("workdir/",)))
    roots.append(_ExportRoot("cwd", Path.cwd(), ("workdir/",)))

    unique: list[_ExportRoot] = []
    seen: set[str] = set()
    for root in roots:
        resolved = str(root.path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(root)
    return unique


def _workspace_relative_path(abs_path: str, workspace_roots: list[_ExportRoot]) -> str:
    path = Path(abs_path).resolve()
    for root in workspace_roots:
        try:
            return _root_relative_path(path, root)
        except ValueError:
            continue
    return path.as_posix().lstrip("/")


def _resolved_export_entry(abs_path: str, workspace_roots: list[_ExportRoot]) -> dict:
    path = Path(abs_path).resolve()
    for root in workspace_roots:
        try:
            relative = _root_relative_path(path, root)
        except ValueError:
            continue
        return {
            "copy_path": str(path),
            "original": str(Path(abs_path)),
            "relative": relative,
            "local": f"files/{relative}",
            "source_root_kind": root.kind,
        }

    relative = _external_relative_path(str(path))
    return {
        "copy_path": str(path),
        "original": str(Path(abs_path)),
        "relative": relative,
        "local": f"files/{relative}",
        "source_root_kind": "external",
    }


def _relative_files_path(abs_path: str, workspace_roots: list[_ExportRoot] | None = None) -> str:
    """Convert an absolute path to a bundle-relative ``files/…`` path."""
    if workspace_roots:
        return _resolved_export_entry(abs_path, workspace_roots)["local"]
    return "files/" + Path(abs_path).as_posix().lstrip("/")


def _file_uri_variants(abs_path: str) -> set[str]:
    variants = {f"file://{abs_path}"}
    try:
        variants.add(Path(abs_path).resolve().as_uri())
    except ValueError:
        pass
    return variants


def _replace_relative_reference(text: str, relative_path: str, replacement: str) -> str:
    if not relative_path:
        return text
    pattern = re.compile(
        rf'(?<![\w./-]){re.escape(relative_path)}(?![\w./-])'
    )
    return pattern.sub(replacement, text)


def _rewrite_export_paths(
    text: str,
    copied_files: list[dict],
    workspace_roots: list[_ExportRoot],
) -> str:
    rewritten = text
    for f in sorted(copied_files, key=lambda item: len(item["original"]), reverse=True):
        original = f["original"]
        replacement = "./" + f["local"]

        for uri in sorted(_file_uri_variants(original), key=len, reverse=True):
            rewritten = rewritten.replace(uri, replacement)

        rewritten = rewritten.replace(original, replacement)
        rewritten = _replace_relative_reference(
            rewritten,
            _workspace_relative_path(original, workspace_roots),
            replacement,
        )
        relative = f.get("relative")
        if isinstance(relative, str) and relative:
            rewritten = _replace_relative_reference(rewritten, relative, replacement)
            rewritten = _replace_relative_reference(
                rewritten, f"workdir/{relative}", replacement
            )
    return rewritten


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"
    }


def _safe_posix_parts(path: str) -> tuple[str, ...]:
    pure = PurePosixPath(path)
    parts = pure.parts
    if parts and parts[0] in ("/", ""):
        parts = parts[1:]
    if parts and parts[0] == ".":
        parts = parts[1:]
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe bundle path: {path}")
    return parts


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_extract_zip(archive_path: Path, dest_dir: Path) -> None:
    import zipfile

    dest_root = dest_dir.resolve()
    with zipfile.ZipFile(str(archive_path), "r") as zf:
        for info in zf.infolist():
            target = (dest_root / info.filename).resolve()
            if not _is_relative_to(target, dest_root):
                raise ValueError(f"Unsafe archive member: {info.filename}")
        zf.extractall(dest_root)


def _safe_extract_tar(archive_path: Path, dest_dir: Path) -> None:
    dest_root = dest_dir.resolve()
    with tarfile.open(str(archive_path), "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest_root / member.name).resolve()
            if not _is_relative_to(target, dest_root):
                raise ValueError(f"Unsafe archive member: {member.name}")
        tar.extractall(dest_root)


def _bundle_source_path(bundle_path: Path, local: str) -> Path:
    parts = _safe_posix_parts(local)
    if not parts:
        raise ValueError("Empty bundle path")
    return bundle_path.joinpath(*parts)


def _strip_files_prefix(local: str) -> PurePosixPath:
    parts = _safe_posix_parts(local)
    if parts and parts[0] == "files":
        parts = parts[1:]
    if not parts:
        raise ValueError(f"Bundle file path has no relative destination: {local}")
    return PurePosixPath(*parts)


def _common_suffix_relative(local_rel: PurePosixPath, original: str) -> PurePosixPath | None:
    local_parts = local_rel.parts
    original_parts = tuple(part for part in PurePosixPath(original).parts if part != "/")
    max_len = min(len(local_parts), len(original_parts))
    for length in range(max_len, 1, -1):
        if local_parts[-length:] == original_parts[-length:]:
            return PurePosixPath(*local_parts[-length:])
    return None


def _restore_relative_path(file_info: dict, local: str) -> PurePosixPath:
    relative = file_info.get("relative")
    if isinstance(relative, str) and relative:
        return PurePosixPath(*_safe_posix_parts(relative))

    local_rel = _strip_files_prefix(local)
    original = file_info.get("original")
    original_workspace = file_info.get("original_workspace")
    if isinstance(original, str) and isinstance(original_workspace, str):
        try:
            original_rel = Path(original).relative_to(Path(original_workspace))
            return PurePosixPath(*_safe_posix_parts(original_rel.as_posix()))
        except ValueError:
            pass
    if isinstance(original, str) and original:
        suffix = _common_suffix_relative(local_rel, original)
        if suffix is not None:
            return suffix

    parts = local_rel.parts
    if len(parts) > 1 and parts[0] in {"workspace", "project"}:
        return PurePosixPath(*parts[1:])
    return local_rel


def _rewrite_import_paths(text: str, local: str, dest: Path) -> str:
    replacement = dest.as_uri() if _is_image_path(dest) else str(dest)
    local_path = PurePosixPath(*_safe_posix_parts(local)).as_posix()
    candidates = {
        f"./{local_path}",
        local_path,
    }
    if local_path.startswith("files/"):
        candidates.add(f"./{local_path}")
    for candidate in sorted(candidates, key=len, reverse=True):
        text = text.replace(candidate, replacement)
    return text


def _set_import_workspace(meta: dict, target_root: Path) -> None:
    extra_data = meta.get("extra_data")
    if not isinstance(extra_data, dict):
        return
    project = extra_data.get("project")
    if not isinstance(project, dict):
        return
    if project.get("workspace_path"):
        project["workspace_path"] = str(target_root)


def _original_workspace_from_meta_text(meta_text: str) -> str:
    try:
        meta = json.loads(meta_text)
    except json.JSONDecodeError:
        return ""
    extra_data = meta.get("extra_data")
    if not isinstance(extra_data, dict):
        return ""
    project = extra_data.get("project")
    if not isinstance(project, dict):
        return ""
    workspace = project.get("workspace_path")
    return workspace if isinstance(workspace, str) else ""


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

    # ---- infer workspace roots ----
    workspace_roots = _workspace_roots(memory_dir, meta)

    # ---- scan for file references ----
    all_entries: dict[str, dict] = {}
    all_entries.update(_scan_jsonl_export_file_paths(jsonl_text, workspace_roots))
    all_entries.update(_scan_json_export_file_paths(meta_text, workspace_roots))
    logger.info(f"[export] Found {len(all_entries)} file references in chat {chat_id}")

    # ---- prepare output ----
    output_dir.mkdir(parents=True, exist_ok=True)
    files_dir = output_dir / "files"
    files_dir.mkdir(exist_ok=True)

    copied_files: list[dict] = []
    skipped_files: list[str] = []
    limit_bytes = int(size_limit_mb * 1024 * 1024)

    for copy_path, entry in sorted(all_entries.items()):
        try:
            file_size = os.path.getsize(copy_path)
        except OSError:
            skipped_files.append(copy_path)
            continue
        rel = entry["local"]
        if file_size > limit_bytes:
            skipped_files.append(copy_path)
            logger.info(f"[export] Skipping large file ({file_size/1e6:.1f}MB): {copy_path}")
            continue
        dest = output_dir / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(copy_path, dest)
        except (PermissionError, OSError) as e:
            skipped_files.append(copy_path)
            logger.warning(f"[export] Cannot copy {copy_path}: {e}")
            continue
        copied_files.append({
            "original": entry["original"],
            "relative": entry["relative"],
            "local": rel,
            "size": file_size,
            "source_root_kind": entry["source_root_kind"],
        })

    # ---- rewrite paths in jsonl/meta ----
    rewritten_jsonl = _rewrite_export_paths(jsonl_text, copied_files, workspace_roots)
    rewritten_meta = _rewrite_export_paths(meta_text, copied_files, workspace_roots)

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
        archive = str(output_dir) + ".zip"
        shutil.make_archive(str(output_dir), "zip", str(output_dir.parent), output_dir.name)
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

    # ---- handle compressed archives ----
    tmp_dir: Optional[str] = None
    if bundle_path.suffix == ".zip":
        tmp_dir = tempfile.mkdtemp(prefix="pantheon_import_")
        try:
            _safe_extract_zip(bundle_path, Path(tmp_dir))
        except ValueError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {"success": False, "message": f"Unsafe archive: {exc}"}
        children = list(Path(tmp_dir).iterdir())
        if len(children) == 1 and children[0].is_dir():
            bundle_path = children[0]
        else:
            bundle_path = Path(tmp_dir)
    elif bundle_path.suffix == ".gz" or str(bundle_path).endswith(".tar.gz"):
        tmp_dir = tempfile.mkdtemp(prefix="pantheon_import_")
        try:
            _safe_extract_tar(bundle_path, Path(tmp_dir))
        except ValueError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {"success": False, "message": f"Unsafe archive: {exc}"}
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
    original_workspace = _original_workspace_from_meta_text(meta_text)

    # ---- copy files and rewrite paths ----
    files_dir = bundle_path / "files"
    files_copied = 0

    manifest_files = manifest.get("files")
    file_entries: list[dict] = []
    if isinstance(manifest_files, list):
        file_entries = [item for item in manifest_files if isinstance(item, dict)]
    elif files_dir.exists():
        for root, _, filenames in os.walk(files_dir):
            for fname in filenames:
                src = Path(root) / fname
                rel_to_bundle = src.relative_to(bundle_path).as_posix()
                file_entries.append({"local": rel_to_bundle})

    if original_workspace:
        for file_info in file_entries:
            file_info.setdefault("original_workspace", original_workspace)

    for file_info in file_entries:
        local = file_info.get("local")
        if not isinstance(local, str) or not local:
            continue
        try:
            src = _bundle_source_path(bundle_path, local)
            relative_dest = _restore_relative_path(file_info, local)
        except ValueError as exc:
            logger.warning(f"[import] Skipping unsafe bundle path {local!r}: {exc}")
            continue
        if not src.is_file():
            logger.warning(f"[import] Bundled file missing: {src}")
            continue

        dest = target_root / Path(*relative_dest.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(src, dest)
            files_copied += 1

        jsonl_text = _rewrite_import_paths(jsonl_text, local, dest)
        meta_text = _rewrite_import_paths(meta_text, local, dest)

    # ---- write chat memory ----
    meta = json.loads(meta_text)
    _set_import_workspace(meta, target_root)
    original_id = manifest.get("chat_id", meta.get("id", ""))
    original_name = meta.get("name", manifest.get("chat_name", "Imported Chat"))

    # If the original chat already exists locally, skip creating a duplicate.
    if original_id and (memory_dir / f"{original_id}.jsonl").exists():
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return {
            "success": True,
            "chat_id": original_id,
            "chat_name": original_name,
            "message": f"Chat '{original_name}' already exists — skipped (files updated)",
        }

    chat_id = original_id if original_id else str(uuid.uuid4())
    meta["id"] = chat_id

    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / f"{chat_id}.jsonl").write_text(jsonl_text, encoding="utf-8")
    (memory_dir / f"{chat_id}.meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Cleanup temp dir
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    logger.info(f"[import] Chat imported as {chat_id} ({original_name}), {files_copied} files restored")
    return {
        "success": True,
        "chat_id": chat_id,
        "chat_name": original_name,
        "message": f"Imported '{original_name}' with {files_copied} files",
    }
