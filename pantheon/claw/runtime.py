"""
PantheonClaw shared channel runtime.

Provides:
  - ``Deduper``        — memory+disk message-ID deduplication
  - ``RunningTask``    — dataclass for in-flight analysis tasks
  - ``text_chunks()``  — paragraph-aware text splitting
  - ``parse_step_progress()`` — extract human-readable progress from
                                pantheon step messages (tool calls,
                                agent transfers, code execution, etc.)
  - ``ChannelRuntime`` — base class for all PantheonClaw channel bots

All channel implementations (Telegram, Discord, Slack, QQ, Feishu,
WeChat, iMessage) inherit from ``ChannelRuntime`` to share the task-
queue management pattern and the rich step-message display logic that
exposes pantheon's multi-agent capabilities to end users.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from pantheon.claw.registry import ConversationRoute
from pantheon.settings import get_settings

logger = logging.getLogger("pantheon.claw.runtime")

_DEDUP_TTL_SECONDS = 24 * 60 * 60
_DEDUP_MAX_ENTRIES = 10_000

# ─── Friendly tool-name map ────────────────────────────────────────────────────
# Maps internal tool/function names to compact human-readable labels shown in
# the channel's live "progress" placeholder.
_TOOL_FRIENDLY: Dict[str, str] = {
    "python": "Python",
    "run_code": "code",
    "execute_code": "code",
    "execute_python": "Python",
    "bash": "shell",
    "shell": "shell",
    "search": "search",
    "search_web": "web search",
    "web_search": "web search",
    "search_documents": "search docs",
    "read_file": "read file",
    "write_file": "write file",
    "list_files": "list files",
    "create_file": "create file",
    "delete_file": "delete file",
    "fetch_url": "fetch URL",
    "http_request": "HTTP",
    "summarize": "summarize",
    "analyze": "analyze",
    "plot": "plot",
    "visualize": "visualize",
}


def _friendly(name: str) -> str:
    return _TOOL_FRIENDLY.get(name, name.replace("_", " "))


# ─── Deduplicator ─────────────────────────────────────────────────────────────

class Deduper:
    """Memory + disk dedup cache for inbound message IDs.

    Used by QQ, Feishu, and any channel where the platform may replay the
    same message ID after reconnects or transient delivery retries.
    """

    def __init__(
        self,
        store_path: Path,
        *,
        ttl_seconds: int = _DEDUP_TTL_SECONDS,
        max_entries: int = _DEDUP_MAX_ENTRIES,
    ) -> None:
        self._store_path = store_path
        self._ttl_seconds = max(0, int(ttl_seconds))
        self._max_entries = max(100, int(max_entries))
        self._lock = threading.Lock()
        self._cache: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            if not self._store_path.exists():
                return
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                now = time.time()
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        if self._ttl_seconds <= 0 or (now - float(v)) < self._ttl_seconds:
                            self._cache[k] = float(v)
                self._prune_locked(now)
        except Exception:
            logger.debug("Failed to load dedupe cache %s", self._store_path, exc_info=True)

    def _prune_locked(self, now: Optional[float] = None) -> None:
        ts_now = now if now is not None else time.time()
        if self._ttl_seconds > 0:
            expired = [k for k, ts in self._cache.items() if (ts_now - ts) >= self._ttl_seconds]
            for k in expired:
                self._cache.pop(k, None)
        if len(self._cache) > self._max_entries:
            keep = sorted(self._cache.items(), key=lambda x: x[1], reverse=True)[: self._max_entries]
            self._cache = dict(keep)

    def _persist_locked(self) -> None:
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._store_path)
        except Exception:
            logger.debug("Failed to persist dedupe cache %s", self._store_path, exc_info=True)

    def seen_or_record(self, key: str) -> bool:
        """Return True if *key* was already seen; always record it."""
        token = (key or "").strip()
        if not token:
            return False
        with self._lock:
            now = time.time()
            self._prune_locked(now)
            seen = token in self._cache
            self._cache[token] = now
            self._persist_locked()
            return seen

    @classmethod
    def for_channel(cls, channel: str) -> "Deduper":
        """Create a channel-specific deduper stored under the pantheon dir."""
        path = get_settings().pantheon_dir / "claw" / channel / "dedup" / "global.json"
        return cls(path)


# ─── RunningTask ──────────────────────────────────────────────────────────────

@dataclass
class RunningTask:
    task: asyncio.Task
    request: str
    started_at: float


# ─── Text chunking ────────────────────────────────────────────────────────────

def text_chunks(text: str, limit: int = 4000) -> List[str]:
    """Split *text* into chunks ≤ *limit* chars, respecting paragraph breaks."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    buf = ""
    for para in text.split("\n\n"):
        cand = f"{buf}\n\n{para}".strip() if buf else para
        if len(cand) <= limit:
            buf = cand
            continue
        if buf:
            chunks.append(buf)
        if len(para) <= limit:
            buf = para
        else:
            pos = 0
            while pos < len(para):
                chunks.append(para[pos : pos + limit])
                pos += limit
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


# ─── Step-message parser ──────────────────────────────────────────────────────

def parse_step_progress(step: Dict[str, Any]) -> Optional[str]:
    """Extract a human-readable progress string from a pantheon step message.

    Pantheon's multi-agent system produces three kinds of step messages:

    1. **Assistant message with tool_calls** — agent is about to call tools
       ```
       {"role": "assistant", "tool_calls": [{"function": {"name": "python"}}]}
       ```

    2. **Tool result** — a tool finished executing
       ```
       {"role": "tool", "tool_name": "python", "content": "..."}
       ```

    3. **Agent transfer** — the current agent handed control to another
       ```
       {"role": "tool", "transfer": True, "content": "<target_agent_name>"}
       ```

    Returns a short string suitable for display in a "thinking" placeholder,
    or None when there is nothing useful to show.
    """
    if not isinstance(step, dict):
        return None

    # Agent transfer ──────────────────────────────────────────────────────────
    if step.get("transfer"):
        agent_name = str(step.get("content") or "").strip()
        return f"→ {agent_name}" if agent_name else "Switching agents..."

    # Pending tool calls (assistant message) ──────────────────────────────────
    tool_calls = step.get("tool_calls") or []
    if tool_calls:
        names: List[str] = []
        for tc in tool_calls:
            raw_name = (
                (tc.get("function") or {}).get("name")
                or tc.get("name")
                or ""
            )
            if raw_name:
                names.append(_friendly(raw_name))
        if names:
            return f"⚙️ {', '.join(names[:3])}"
        return "⚙️ Running tools..."

    # Completed tool result ────────────────────────────────────────────────────
    if step.get("role") == "tool" and not step.get("transfer"):
        tool_name = str(step.get("tool_name") or step.get("name") or "").strip()
        if tool_name:
            duration = (step.get("_metadata") or {}).get("execution_duration")
            if isinstance(duration, (int, float)) and duration > 0:
                return f"✓ {_friendly(tool_name)} ({duration:.1f}s)"
            return f"✓ {_friendly(tool_name)}"

    return None


def parse_step_text(step: Dict[str, Any]) -> Optional[str]:
    """Return the assistant text from a step message, or None."""
    if step.get("role") == "assistant":
        return str(step.get("content") or "") or None
    return None


# ─── ChannelRuntime ──────────────────────────────────────────────────────────

class ChannelRuntime:
    """Base class for all PantheonClaw channel bot implementations.

    Provides shared:
    - per-route task queue management (``_tasks``, ``_pending``)
    - analysis wrapper skeleton with streaming callbacks
    - access to ``bridge.handle_control_command`` for slash commands
    - step message parsing (``parse_step_progress``)

    Sub-classes must implement ``_send_progress`` and ``_send_final`` (or
    override ``_analysis_wrapper`` entirely) for channel-specific delivery.

    Usage example
    -------------
    ```python
    class MyChannelBot(ChannelRuntime):
        async def _send_progress(self, target, text: str) -> None:
            ...  # update placeholder / send interim message
        async def _send_final(self, target, text: str) -> None:
            ...  # send completed response
    ```
    """

    def __init__(self, *, bridge: Any) -> None:
        self._bridge = bridge
        self._tasks: Dict[str, RunningTask] = {}
        self._pending: Dict[str, List[str]] = {}

    # ── Task helpers ─────────────────────────────────────────────────────────

    def _get_running(self, route_key: str) -> Optional[RunningTask]:
        rt = self._tasks.get(route_key)
        return rt if rt is not None and not rt.task.done() else None

    def _set_task(self, route_key: str, task: asyncio.Task, request: str) -> None:
        self._tasks[route_key] = RunningTask(task=task, request=request, started_at=time.time())

    def _pop_task(self, route_key: str) -> None:
        self._tasks.pop(route_key, None)

    def _queue_message(self, route_key: str, text: str) -> None:
        self._pending.setdefault(route_key, []).append(text)

    def _pop_queued(self, route_key: str) -> List[str]:
        return self._pending.pop(route_key, [])

    def _clear_pending(self, route_key: str) -> None:
        self._pending.pop(route_key, None)

    # ── Static helpers (available without instance) ───────────────────────────

    parse_step_progress = staticmethod(parse_step_progress)
    parse_step_text = staticmethod(parse_step_text)
    text_chunks = staticmethod(text_chunks)

    # ── Bridge callbacks ──────────────────────────────────────────────────────

    def make_chunk_callback(
        self,
        buf: List[str],
        on_update: Optional[Callable[[], Coroutine]] = None,
    ) -> Callable[[Dict[str, Any]], Coroutine]:
        """Return an ``on_chunk`` callback that appends to *buf* and optionally
        triggers a UI refresh via *on_update*."""
        async def _on_chunk(chunk: Dict[str, Any]) -> None:
            text = str(chunk.get("content") or "")
            if text:
                buf.append(text)
            if on_update is not None:
                await on_update()
        return _on_chunk

    def make_step_callback(
        self,
        llm_buf: List[str],
        progress_cb: Optional[Callable[[str], Coroutine]] = None,
        refresh_cb: Optional[Callable[[], Coroutine]] = None,
    ) -> Callable[[Dict[str, Any]], Coroutine]:
        """Return an ``on_step`` callback that:
        - Updates *llm_buf* with assistant text
        - Calls *progress_cb(label)* for tool/transfer events
        - Calls *refresh_cb()* to push updates to the channel UI
        """
        async def _on_step(step: Dict[str, Any]) -> None:
            # Overwrite LLM buffer with the full assistant message
            txt = parse_step_text(step)
            if txt:
                llm_buf.clear()
                llm_buf.append(txt)

            # Show tool/agent-transfer progress
            progress = parse_step_progress(step)
            if progress is not None and progress_cb is not None:
                await progress_cb(progress)

            if refresh_cb is not None:
                await refresh_cb()

        return _on_step
