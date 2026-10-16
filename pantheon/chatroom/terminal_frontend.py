"""The Agent App's terminal frontend, served to `pantheon agent chat`
(docs/pantheon-cli.md, "The terminal chat is a frontend of the Agent App").

The terminal chat is this release's own code: ``terminal.py`` with the
package's Python sources and its hash-pinned ``requirements.txt``. The App
serves exactly the files it runs, so the terminal always matches the running
revision. The archive is built once per process and read in small chunks.
"""
from __future__ import annotations

import base64
import functools
import gzip
import hashlib
import io
import tarfile
from pathlib import Path

CHUNK = 512 * 1024


def package_root() -> Path | None:
    """The Agent package this process runs from, when it is one."""
    import pantheon
    vendor = Path(pantheon.__file__).resolve().parent.parent
    root = vendor.parent.parent
    if vendor.name != "_vendor" or vendor.parent.name != "backend":
        return None
    if not all((root / name).is_file() for name in ("terminal.py", "requirements.txt", "app.json")):
        return None
    return root


def _wanted(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if "__pycache__" in info.name.split("/") or info.name.endswith(".pyc"):
        return None
    if info.issym() or info.islnk():
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


@functools.lru_cache(maxsize=1)
def bundle() -> tuple[bytes, str]:
    """The terminal frontend as a tar.gz, and its sha256."""
    root = package_root()
    if root is None:
        raise RuntimeError("This Agent was not started from a release package; it has no terminal frontend")
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for name in ("terminal.py", "requirements.txt", "app.json"):
            tar.add(root / name, arcname=name, filter=_wanted)
        tar.add(root / "backend" / "_vendor", arcname="backend/_vendor", filter=_wanted)
    data = raw.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def chunk(offset: int) -> dict:
    data, digest = bundle()
    if not isinstance(offset, int) or not 0 <= offset <= len(data):
        raise ValueError("Read the terminal frontend from an offset within it")
    part = data[offset:offset + CHUNK]
    return {"sha256": digest, "size": len(data), "offset": offset,
            "data": base64.b64encode(part).decode()}
