"""Brain-state sync — the worker's durable state rides a tarball via the Hub.

Topology deployments run this worker on a fast-boot node with ephemeral
disk, while the user's volume lives on the workspace node. Chats, memory
and settings (``.pantheon/``) are small — hundreds of KB — so instead of
paying a network filesystem on every message, the worker keeps a local
working copy and syncs a tar.gz through the Hub, which owns the volume
credentials:

- on boot: ``restore()`` GETs the last snapshot and unpacks it (files are
  overwritten, never deleted — state the user created on THIS node since
  the snapshot survives and rejoins the next push);
- while running: ``push_loop()`` walks the state dirs every few seconds
  and PUTs a fresh tarball whenever the digest changes.

Both ends are inert unless ``PANTHEON_STATE_URL`` and
``PANTHEON_STATE_TOKEN`` are set (the Hub injects them for topology
brains), so local runs and classic sandboxes are untouched.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
import time
import urllib.request
from pathlib import Path

from pantheon.utils.log import logger

#: State roots relative to home, mirroring the two volume layouts (the
#: project layer under default_workspace when that mode is on, the root
#: layer otherwise). Whichever exist are packed.
STATE_DIRS = (".pantheon", "default_workspace/.pantheon")

#: WHITELIST of what counts as worker state, relative to a state root.
#: .pantheon on a long-lived volume accumulates non-state bulk — pip
#: packages, browser profiles, exports (100s of MB) — so packing is
#: opt-in, not opt-out. New durable worker state must be added here.
#: Keep in sync with pantheon_hub/api/user_state.py.
INCLUDE_DIRS = ("memory/", "memory-store/", "agents/", "teams/",
                "prompts/", "skills/", "apps/", "brain/")
INCLUDE_FILES = ("settings.json", "mcp.json", "projects.json",
                 "MEMORY.md", "on-start.sh")

#: Junk that can appear inside whitelisted dirs.
DENY = (".nats-", "__pycache__", ".lock", ".tmp")

MAX_TAR_BYTES = 64 * 1024 * 1024
PUSH_INTERVAL_SECS = 15.0


def _config() -> tuple[str, str] | None:
    url = os.environ.get("PANTHEON_STATE_URL", "").strip()
    token = os.environ.get("PANTHEON_STATE_TOKEN", "").strip()
    return (url, token) if url and token else None


def _state_rel(rel: str) -> str | None:
    """Path relative to its state root, or None if outside every root."""
    for root in STATE_DIRS:
        pfx = root + "/"
        if rel.startswith(pfx):
            return rel[len(pfx):]
    return None


def _keep(rel: str) -> bool:
    sub = _state_rel(rel)
    if sub is None or any(x in sub for x in DENY):
        return False
    return sub in INCLUDE_FILES or any(sub.startswith(d) for d in INCLUDE_DIRS)


def _walk(home: Path):
    """Yield (abs_path, rel_path) for every file in the state dirs."""
    for root in STATE_DIRS:
        base = home / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(home).as_posix()
            if _keep(rel):
                yield p, rel


def _digest(home: Path) -> str:
    h = hashlib.sha256()
    for p, rel in _walk(home):
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{rel}:{st.st_mtime_ns}:{st.st_size}\n".encode())
    return h.hexdigest()


def _pack(home: Path) -> bytes | None:
    buf = io.BytesIO()
    n = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p, rel in _walk(home):
            try:
                tar.add(p, arcname=rel, recursive=False)
                n += 1
            except OSError:
                continue
    data = buf.getvalue()
    if len(data) > MAX_TAR_BYTES:
        logger.error(
            f"[state-sync] snapshot is {len(data)/1e6:.0f}MB (> "
            f"{MAX_TAR_BYTES/1e6:.0f}MB) — not pushing. Something bulky "
            "landed in .pantheon; check EXCLUDES.")
        return None
    logger.debug(f"[state-sync] packed {n} files, {len(data)/1024:.0f}KB")
    return data


def _request(method: str, url: str, token: str, body: bytes | None = None,
             timeout: float = 60.0):
    req = urllib.request.Request(url, data=body, method=method, headers={
        "X-State-Token": token,
        "Content-Type": "application/gzip",
    })
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (hub URL from env)


#: Whether a restore round-trip has SUCCEEDED (204-no-snapshot counts:
#: the Hub answered authoritatively). While False, pushing would risk
#: writing a snapshot that predates the state we failed to fetch, so the
#: push loop re-tries the restore first.
_restored = False


def restore(home: Path | None = None) -> bool:
    """Unpack the last snapshot from the Hub into home. Never deletes.

    Runs synchronously before the ChatRoom exists so the memory manager
    opens the restored store, not an empty one. A missing snapshot (new
    user) and any transport error both leave the tree as-is.
    """
    global _restored
    cfg = _config()
    if cfg is None:
        return False
    url, token = cfg
    home = home or Path.cwd()
    t0 = time.monotonic()
    try:
        with _request("GET", url, token) as resp:
            if resp.status == 204:
                logger.info("[state-sync] no snapshot yet (fresh user)")
                _restored = True
                return False
            data = resp.read()
    except Exception as e:
        logger.warning(f"[state-sync] restore failed (starting empty): {e}")
        return False
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(home, filter="data")
        logger.info(
            f"[state-sync] restored {len(data)/1024:.0f}KB of state in "
            f"{time.monotonic()-t0:.1f}s")
        _restored = True
        return True
    except Exception as e:
        logger.error(f"[state-sync] snapshot unpack failed: {e}")
        return False


async def push_loop(home: Path | None = None) -> None:
    """Push a fresh snapshot whenever the state digest changes."""
    cfg = _config()
    if cfg is None:
        return
    url, token = cfg
    home = home or Path.cwd()
    # Seed with the post-restore digest so a boot with no user activity
    # pushes nothing.
    last = await asyncio.to_thread(_digest, home)
    while True:
        await asyncio.sleep(PUSH_INTERVAL_SECS)
        try:
            cur = await asyncio.to_thread(_digest, home)
            if cur == last:
                continue
            # Never push over state we failed to fetch: a snapshot written
            # now would predate what's on the volume and shadow it forever
            # (the Hub only falls back to loose files while NO snapshot
            # exists). Re-try the restore until the Hub answers.
            if not _restored:
                await asyncio.to_thread(restore, home)
                if not _restored:
                    logger.warning("[state-sync] holding push until a "
                                   "restore round-trip succeeds")
                    continue
                cur = await asyncio.to_thread(_digest, home)
            data = await asyncio.to_thread(_pack, home)
            if data is None:
                last = cur  # don't retry an oversized tree every tick
                continue

            def _put():
                with _request("PUT", url, token, body=data) as resp:
                    return resp.status

            status = await asyncio.to_thread(_put)
            if status in (200, 204):
                last = cur
            else:
                logger.warning(f"[state-sync] push got HTTP {status}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[state-sync] push failed (will retry): {e}")
