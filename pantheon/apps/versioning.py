"""App versioning: one git repo per App, publish = tag, install = clone.

The version DAG is git's — fork+merge, refs as branches, parents in the
commit graph — so an agent needs no bespoke tooling to evolve an App:
plain git in its sandbox is the whole mechanism (§ resolved 2026-08-28).
This module is the thin porcelain around that decision:

  fork      copy an installed App into a writable scope and git-init it,
            recording where it came from;
  publish   run check-compat against the last published tag, refuse a
            version that lies about the change, tag `v<semver>`;
  install   clone (or copy) a source into a scope, then walk
            manifest.dependencies and install the closure — flat
            resolution: one version per app id, ranges must agree.

A "forge" here is any place `git clone` accepts — a directory of bare
repos on disk serves the tests and a laptop; the store's git hosting
speaks the same protocol when it lands (its server side lives in the
pantheon-store repo).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pantheon.utils.log import logger

from .compat import CompatReport, _parse_semver, check_compat
from .schema import AppManifest, parse_manifest

MANIFEST_NAMES = ("app.json", "atrium.json")


class VersioningError(RuntimeError):
    pass


# ── semver ranges ───────────────────────────────────────────────────────────

def _match_range(version: str, range_: str) -> bool:
    """Does `version` satisfy `range_`? Supported: "*", exact, ">=x.y.z",
    "^x.y.z" (same major, at least this), "~x.y.z" (same major.minor)."""
    if not range_ or range_ == "*":
        return True
    v = _parse_semver(version)
    if range_.startswith(">="):
        return v >= _parse_semver(range_[2:])
    if range_.startswith("^"):
        base = _parse_semver(range_[1:])
        return v[0] == base[0] and v >= base
    if range_.startswith("~"):
        base = _parse_semver(range_[1:])
        return v[:2] == base[:2] and v >= base
    return v == _parse_semver(range_)


def _bump(version: str, level: str) -> str:
    major, minor, patch = _parse_semver(version)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


# ── git plumbing (subprocess: the sandbox's git IS the implementation) ──────

def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise VersioningError(
            f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def _read_manifest(app_dir: Path) -> AppManifest:
    for name in MANIFEST_NAMES:
        p = app_dir / name
        if p.is_file():
            return parse_manifest(json.loads(p.read_text()))
    raise VersioningError(f"no app manifest under {app_dir}")


def _manifest_at(app_dir: Path, ref: str) -> AppManifest | None:
    """The manifest as a git ref has it, or None if the ref lacks one."""
    for name in MANIFEST_NAMES:
        try:
            raw = _git("show", f"{ref}:{name}", cwd=app_dir)
        except VersioningError:
            continue
        return parse_manifest(json.loads(raw))
    return None


def last_published_tag(app_dir: Path) -> str | None:
    """The highest v<semver> tag, by version order (not tag date)."""
    try:
        tags = _git("tag", "--list", "v*", cwd=app_dir).splitlines()
    except VersioningError:
        return None
    versioned = [(t, _parse_semver(t[1:])) for t in tags if t.startswith("v")]
    if not versioned:
        return None
    return max(versioned, key=lambda tv: tv[1])[0]


# ── porcelain ───────────────────────────────────────────────────────────────

def fork(src_dir: Path, dst_root: Path, new_id: str | None = None) -> Path:
    """Copy an App into a writable scope and git-init it as its own repo.

    The origin is recorded in the first commit message; a same-id fork into
    a user scope shadows the original on headed faces (that IS dev mode), a
    renamed fork is a new App. Refuses to overwrite.
    """
    manifest = _read_manifest(src_dir)
    app_id = new_id or manifest.id
    dst = dst_root / app_id
    if dst.exists():
        raise VersioningError(f"{dst} already exists")
    shutil.copytree(src_dir, dst, ignore=shutil.ignore_patterns(
        "__pycache__", ".git", "*.pyc"))
    if new_id:
        p = dst / "app.json"
        data = json.loads(p.read_text()) if p.is_file() else json.loads(
            (dst / "atrium.json").read_text())
        data["id"] = new_id
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        (dst / "atrium.json").unlink(missing_ok=True)
    if not (dst / ".git").is_dir():
        _git("init", "-q", cwd=dst)
    _git("add", "-A", cwd=dst)
    _git("-c", "user.email=apps@pantheon", "-c", "user.name=pantheon",
         "commit", "-q", "-m",
         f"fork of {manifest.id}@{manifest.version} from {src_dir}",
         cwd=dst)
    _git("tag", f"v{manifest.version}", cwd=dst)
    return dst


def publish_check(app_dir: Path) -> tuple[CompatReport | None, str | None]:
    """The publish gate's evidence: (compat vs last tag, that tag).

    None report = first publish, nothing to hold the tree against.
    """
    tag = last_published_tag(app_dir)
    if tag is None:
        return None, None
    old = _manifest_at(app_dir, tag)
    if old is None:
        return None, tag
    return check_compat(old, _read_manifest(app_dir)), tag


def publish(app_dir: Path, allow_first: bool = True) -> str:
    """Gate, commit, tag. Returns the new tag.

    Refuses when check-compat finds violations — the exact "who breaks on
    which signature" list an agent needs BEFORE the tag exists, not after
    dependents crash.
    """
    manifest = _read_manifest(app_dir)
    report, tag = publish_check(app_dir)
    if report is not None and not report.ok:
        raise VersioningError(
            f"publish refused (vs {tag}):\n{report.render()}")
    if report is None and not allow_first:
        raise VersioningError("no published tag to check against")
    new_tag = f"v{manifest.version}"
    existing = _git("tag", "--list", new_tag, cwd=app_dir)
    if existing:
        raise VersioningError(
            f"{new_tag} is already published — bump the manifest version")
    if _git("status", "--porcelain", cwd=app_dir):
        _git("add", "-A", cwd=app_dir)
        _git("-c", "user.email=apps@pantheon", "-c", "user.name=pantheon",
             "commit", "-q", "-m", f"publish {manifest.id} {new_tag}",
             cwd=app_dir)
    _git("tag", new_tag, cwd=app_dir)
    return new_tag


def _installed_manifests(scopes: list[Path]) -> dict[str, AppManifest]:
    found: dict[str, AppManifest] = {}
    for root in scopes:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            try:
                m = _read_manifest(d)
            except (VersioningError, Exception):
                continue
            found.setdefault(m.id, m)
    return found


def install(
    source: str,
    dst_root: Path,
    *,
    resolve: "callable[[str], str] | None" = None,
    check_scopes: list[Path] | None = None,
    _depth: int = 0,
) -> list[Path]:
    """Clone `source` into the scope, then install its dependency closure.

    `source` is anything git clones (a bare-repo path, a URL) or a plain
    App directory. `resolve` maps a dependency's app id to its source —
    the forge index; without one, dependencies must already be installed.
    Flat resolution: the first version wins per id, and every range must
    accept what is (or gets) installed.
    """
    if _depth > 16:
        raise VersioningError("dependency chain deeper than 16 — cycle?")
    dst_root.mkdir(parents=True, exist_ok=True)

    src = Path(source)
    if src.is_dir() and any((src / n).is_file() for n in MANIFEST_NAMES):
        m = _read_manifest(src)
        dst = dst_root / m.id
        if not dst.exists():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        tmp = dst_root / f".installing-{_depth}"
        shutil.rmtree(tmp, ignore_errors=True)
        _git("clone", "-q", "--depth", "1", str(source), str(tmp), cwd=dst_root)
        m = _read_manifest(tmp)
        dst = dst_root / m.id
        if dst.exists():
            shutil.rmtree(tmp)
        else:
            tmp.rename(dst)
    installed = [dst]

    scopes = [dst_root, *(check_scopes or [])]
    have = _installed_manifests(scopes)
    for dep_id, spec in _read_manifest(dst).dependencies.items():
        got = have.get(dep_id)
        if got is not None:
            if not _match_range(got.version, spec.range):
                raise VersioningError(
                    f"{m.id} needs {dep_id} {spec.range}, "
                    f"but {got.version} is installed (flat resolution)")
            continue
        if resolve is None:
            raise VersioningError(
                f"{m.id} depends on {dep_id} ({spec.range}) which is not "
                f"installed, and no source index was given")
        installed += install(
            resolve(dep_id), dst_root,
            resolve=resolve, check_scopes=check_scopes, _depth=_depth + 1)
        got = _installed_manifests(scopes).get(dep_id)
        if got is None or not _match_range(got.version, spec.range):
            raise VersioningError(
                f"installing {dep_id} did not satisfy {spec.range} "
                f"(got {got.version if got else 'nothing'})")
    logger.info(f"[apps] installed {m.id}@{m.version} (+{len(installed) - 1} deps)")
    return installed
