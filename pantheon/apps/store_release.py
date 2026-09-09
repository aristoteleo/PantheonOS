"""Portable Store release format: a self-contained Git bundle at an immutable tag.

This stdlib-only protocol module is mirrored in pantheon-hub. No checkout,
hooks, builds, or App code run when the Store validates an upload.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_TREE_BYTES = 128 * 1024 * 1024
SEMVER = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root,
        capture_output=True, timeout=45, env=env,
    )
    if proc.returncode:
        raise ValueError(proc.stderr.decode(errors="replace").strip() or "Git operation failed")
    return proc.stdout.decode("utf-8")


def validate_manifest(manifest: dict, version: str | None = None) -> dict:
    if not isinstance(manifest, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(manifest.get("id", ""))):
        raise ValueError("App manifest requires a valid id")
    if not re.fullmatch(SEMVER, str(manifest.get("version", ""))):
        raise ValueError("App manifest requires a semantic version")
    if version is not None and manifest["version"] != version:
        raise ValueError("Release version does not match the App manifest")
    if not isinstance(manifest.get("apiVersion", 1), int) or not isinstance(manifest.get("atriumApi", 1), int):
        raise ValueError("App API version must be an integer")
    if manifest.get("apiVersion", 1) > 2 or manifest.get("atriumApi", 1) > 1:
        raise ValueError("App requires a newer Atrium API")
    if not isinstance(manifest.get("entry"), dict) or not any(manifest["entry"].values()):
        raise ValueError("App manifest requires an entry point")
    for name in ("frontend", "backend"):
        if manifest["entry"].get(name) is not None and not isinstance(manifest["entry"][name], str):
            raise ValueError("App entry points must be strings")
    return manifest


def release_icon(root: Path, manifest: dict) -> str | None:
    """Small, self-contained catalog icon; never reuse a temporary tunnel URL."""
    icon = manifest.get("icon")
    relative = icon.get("path") if isinstance(icon, dict) else None
    if not isinstance(relative, str):
        return None
    path = (root / relative).resolve()
    mime = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower())
    if not mime or not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > 256 * 1024:
        return None
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def unpack_release(release: dict, destination: Path, version: str) -> dict:
    """Verify bundle/tag/commit/tree and leave a detached, usable Git repository."""
    if release.get("format") != "git-bundle-v1":
        raise ValueError("Unsupported App release format")
    if not re.fullmatch(SEMVER, version) or release.get("tag") != f"v{version}":
        raise ValueError("App release must use tag v<version>")
    commit = str(release.get("commit", ""))
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit):
        raise ValueError("App release requires a full commit SHA")
    encoded = release.get("bundle", "")
    if not isinstance(encoded, str) or len(encoded) > MAX_BUNDLE_BYTES * 4 // 3 + 4:
        raise ValueError("App Git bundle exceeds 32 MiB")
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > MAX_BUNDLE_BYTES or hashlib.sha256(raw).hexdigest() != release.get("sha256"):
        raise ValueError("App Git bundle checksum mismatch")
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / "release.bundle"
        bundle.write_bytes(raw)
        git(destination, "init", "-q")
        git(destination, "bundle", "verify", str(bundle))
        tag = release["tag"]
        git(destination, "fetch", "-q", str(bundle), f"refs/tags/{tag}:refs/tags/{tag}")
        actual = git(destination, "rev-parse", f"refs/tags/{tag}^{{commit}}").strip()
        if actual != commit:
            raise ValueError("App tag does not resolve to the recorded commit")
        # Only reachable version tags are distributed. Unreleased branches
        # stay private in the author's repository; merge ancestry is intact.
        for line in git(destination, "bundle", "list-heads", str(bundle)).splitlines():
            _, ref = line.split(" ", 1)
            if ref != f"refs/tags/{tag}" and re.fullmatch(f"refs/tags/v{SEMVER}", ref):
                git(destination, "fetch", "-q", str(bundle), f"{ref}:{ref}")
                git(destination, "merge-base", "--is-ancestor", f"{ref}^{{commit}}", commit)
        paths, size = set(), 0
        for record in git(destination, "ls-tree", "-rlz", commit).split("\0"):
            if not record:
                continue
            info, path = record.split("\t", 1)
            mode, kind, _, nbytes = info.split()
            parts = PurePosixPath(path).parts
            if kind != "blob" or mode not in ("100644", "100755") or ".." in parts or any(p.lower() == ".git" for p in parts):
                raise ValueError(f"Unsupported App file: {path}")
            size += int(nbytes)
            paths.add(path)
            if size > MAX_TREE_BYTES or len(paths) > 10000:
                raise ValueError("App file tree exceeds the Store limit")
        name = next((n for n in ("app.json", "atrium.json") if n in paths), None)
        if name is None:
            raise ValueError("App repository must have a manifest at its root")
        manifest = validate_manifest(json.loads(git(destination, "show", f"{commit}:{name}")), version)
        frontend = manifest["entry"].get("frontend")
        if frontend and not frontend.startswith("ui:") and frontend not in paths:
            raise ValueError(f"Frontend entry is missing from the release: {frontend}")
        git(destination, "checkout", "-q", "--detach", commit)
        return manifest


def prepare_release(root: Path) -> dict:
    if Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve() != root.resolve():
        raise ValueError("Create a user copy first: each App needs its own Git repository")
    if git(root, "status", "--porcelain").strip():
        raise ValueError("Commit App changes before preparing a release")
    name = next((n for n in ("app.json", "atrium.json") if (root / n).is_file()), None)
    if name is None:
        raise ValueError("App manifest not found")
    manifest = validate_manifest(json.loads((root / name).read_text()))
    tag = f"v{manifest['version']}"
    commit = git(root, "rev-parse", "HEAD").strip()
    if git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}").strip() != commit:
        raise ValueError(f"Tag {tag} must point to the current commit")
    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / "release.bundle"
        tags = [f"refs/tags/{value}" for value in git(root, "tag", "--merged", commit).splitlines()
                if re.fullmatch(f"v{SEMVER}", value)]
        git(root, "bundle", "create", str(bundle), *tags)
        raw = bundle.read_bytes()
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("App Git bundle exceeds 32 MiB")
        release = {"format": "git-bundle-v1", "tag": tag, "commit": commit,
                   "sha256": hashlib.sha256(raw).hexdigest(), "bundle": base64.b64encode(raw).decode()}
        unpack_release(release, Path(temp) / "check", manifest["version"])
    readme = next(((root / n).read_text() for n in ("README.md", "readme.md") if (root / n).is_file()), "")
    return {"manifest": manifest, "app_release": release, "content": readme,
            "bundle_bytes": len(raw), "file_count": len(git(root, "ls-tree", "-rz", "--name-only", "HEAD").split("\0")) - 1}


def git_history(root: Path, limit: int = 100) -> dict:
    """Real topology and refs, bounded for the Store UI."""
    limit = max(1, min(int(limit), 1000))
    fields = git(root, "log", "--all", "HEAD", "--topo-order", f"--max-count={limit + 1}",
                 "-z", "--format=%H%x00%P%x00%an%x00%aI%x00%s").split("\0")
    commits = []
    for i in range(0, len(fields) - 4, 5):
        sha, parents, author, date, subject = fields[i:i + 5]
        commits.append({"id": sha, "parents": parents.split(), "author": author,
                        "date": date, "subject": subject})
    refs = []
    for line in git(root, "for-each-ref", "--format=%(refname)%09%(objectname)%09%(*objectname)",
                    "refs/heads", "refs/remotes", "refs/tags").splitlines():
        name, sha, peeled = line.split("\t")
        refs.append({"name": name, "commit": peeled or sha})
    return {"success": True, "commits": commits[:limit], "refs": refs,
            "head": git(root, "rev-parse", "HEAD").strip(), "has_more": len(commits) > limit}
