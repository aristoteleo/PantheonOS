"""Authenticated, session-scoped IPC with the *visible* QuPath JVM.

No network listener or second QuPath process is involved. A request ID is an
idempotency key for the lifetime of the JVM. A caller timeout never resubmits or
cancels a script: inspect that same ID to discover its eventual outcome.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any
import uuid

_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$")
_TERMINAL = {"succeeded", "failed", "expired"}
_MAX_REQUEST = 600_000


class QuPathBridge:
    """One instance per native session, retained by its process manager."""

    def __init__(self, directory: Path, session_id: str):
        if not _ID.fullmatch(session_id):
            raise ValueError("Invalid native session ID")
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.directory.chmod(0o700)
        self.session_id = session_id
        self._token = secrets.token_urlsafe(32)

    def launch_environment(self) -> dict[str, str]:
        """Merge into child env, then append startup_option() to JAVA_TOOL_OPTIONS."""
        return {
            "PANTHEON_QUPATH_BRIDGE_DIR": str(self.directory),
            "PANTHEON_QUPATH_BRIDGE_SESSION": self.session_id,
            "PANTHEON_QUPATH_BRIDGE_TOKEN": self._token,
        }

    @staticmethod
    def startup_option() -> str:
        script = Path(__file__).with_name("bridge") / "startup.groovy"
        path = str(script.resolve()).replace("\\", "\\\\").replace('"', '\\"')
        return f'-Dqupath.startup.script="{path}"'

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        if value.get("session_id") != self.session_id:
            raise RuntimeError("QuPath bridge session identity mismatch")
        return value

    def ready(self) -> dict[str, Any] | None:
        return self._read(self.directory / "ready.json")

    def request_status(self, request_id: str) -> dict[str, Any]:
        self._validate_id(request_id)
        status = self._read(self.directory / f"{request_id}.response.json")
        if status is not None:
            return status
        if (self.directory / f"{request_id}.request.json").exists():
            return {"session_id": self.session_id, "request_id": request_id,
                    "state": "queued"}
        return {"session_id": self.session_id, "request_id": request_id,
                "state": "unknown"}

    @staticmethod
    def _validate_id(request_id: str):
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            raise ValueError("Invalid request_id")

    async def call(self, method: str, params: dict | None = None, *,
                   request_id: str | None = None, timeout: float = 10.0,
                   queue_timeout: float = 300.0) -> dict[str, Any]:
        """Submit once and wait at most timeout seconds (0–120).

        script params: script (Groovy), thread ('worker' or 'fx'), args (strings),
        optional expected_image (opaque state image_token, or null for no image),
        update_hierarchy (bool, default true; false skips automatic notification).
        state params: annotation_limit (0–2000). Long scripts should use worker;
        JavaFX node mutations must explicitly use fx or Platform.runLater.
        """
        if method not in {"state", "script"}:
            raise ValueError("Unsupported bridge method")
        if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 120:
            raise ValueError("timeout must be between 0 and 120 seconds")
        if not isinstance(queue_timeout, (int, float)) or not 1 <= queue_timeout <= 3600:
            raise ValueError("queue_timeout must be between 1 and 3600 seconds")
        params = dict(params or {})
        if method == "script":
            if not isinstance(params.get("script"), str) or not params["script"].strip():
                raise ValueError("script must be nonempty Groovy source")
            if params.get("thread", "worker") not in {"worker", "fx"}:
                raise ValueError("thread must be worker or fx")
            if not isinstance(params.get("args", []), list) or not all(
                isinstance(arg, str) for arg in params.get("args", [])
            ):
                raise ValueError("args must be an array of strings")
            if "update_hierarchy" in params and not isinstance(params["update_hierarchy"], bool):
                raise ValueError("update_hierarchy must be a boolean")
            if "expected_image" in params and params["expected_image"] is not None:
                expected = params["expected_image"]
                if not isinstance(expected, str) or not _ID.fullmatch(expected):
                    raise ValueError("expected_image must be an image_token from state, or null")
        else:
            limit = params.get("annotation_limit", 200)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 2000:
                raise ValueError("annotation_limit must be an integer between 0 and 2000")
        request_id = request_id or uuid.uuid4().hex
        self._validate_id(request_id)
        request = {"session_id": self.session_id, "token": self._token,
                   "request_id": request_id, "method": method, "params": params,
                   "expires_at": time.time() + queue_timeout}
        raw = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > _MAX_REQUEST:
            raise ValueError("QuPath bridge request exceeds 600 KB")
        path = self.directory / f"{request_id}.request.json"
        # Atomic hard-link publication provides CREATE_NEW semantics without
        # exposing partial JSON to the JVM scanner. Retain requests for dedup.
        temporary = self.directory / f".{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
            try:
                os.link(temporary, path)
            except FileExistsError:
                old = self._read(path)
                if old is None or any(old.get(k) != request[k] for k in (
                    "method", "params", "token"
                )):
                    raise ValueError("request_id already belongs to a different request") from None
        finally:
            temporary.unlink(missing_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            status = self.request_status(request_id)
            if status["state"] in _TERMINAL:
                return status
            if time.monotonic() >= deadline:
                return {**status, "wait_timed_out": True,
                        "message": "Query this request_id; do not repeat the script with a new ID."}
            await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
