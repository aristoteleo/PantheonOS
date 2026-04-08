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

import base64
import mimetypes

from pantheon.claw.registry import ConversationRoute
from pantheon.settings import get_settings

from pantheon.utils.log import logger

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


def extract_display_text(result: Dict[str, Any], llm_buf: List[str]) -> str:
    """Extract human-readable text from a chat result.

    Handles normal responses and interrupt/plan results where the response
    field may contain a raw JSON-like string from a tool result.
    """
    response = result.get("response") or ""

    # Detect interrupt/notify_user tool results embedded in the response.
    # These look like: {"success": true, "interrupt": true, "message": "..."}
    # We want the "message" field, not the raw dict string.
    if isinstance(response, str) and '"interrupt"' in response:
        import json as _json
        try:
            parsed = _json.loads(response)
            if isinstance(parsed, dict) and parsed.get("interrupt"):
                msg = parsed.get("message", "")
                questions = parsed.get("questions", [])
                parts = [msg] if msg else []
                if questions:
                    parts.append("\n".join(f"• {q}" for q in questions))
                if parts:
                    return "\n\n".join(parts)
        except (ValueError, TypeError):
            pass

    if response:
        return str(response)
    return "".join(llm_buf).strip() or "Done."


# ─── Markdown format converters ──────────────────────────────────────────────

import re as _re


def md_to_slack(text: str) -> str:
    """Convert Markdown to Slack mrkdwn format."""
    blocks: List[str] = []

    def _stash_block(m: _re.Match) -> str:
        blocks.append(m.group(0))
        return f"\x00BLOCK{len(blocks) - 1}\x00"

    text = _re.sub(r"```[\s\S]*?```", _stash_block, text)

    inlines: List[str] = []

    def _stash_inline(m: _re.Match) -> str:
        inlines.append(m.group(0))
        return f"\x00INLINE{len(inlines) - 1}\x00"

    text = _re.sub(r"`[^`]+`", _stash_inline, text)

    text = _re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", "", text)  # Remove image links (sent separately)
    text = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    # Bold: **text** → stash, then restore as *text* after italic pass
    bolds: List[str] = []

    def _stash_bold(m: _re.Match) -> str:
        bolds.append(m.group(1))
        return f"\x00BOLD{len(bolds) - 1}\x00"

    text = _re.sub(r"\*\*(.+?)\*\*", _stash_bold, text)
    # Italic: *text* → _text_ (now safe since ** already stashed)
    text = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"_\1_", text)
    # Restore bold as Slack bold
    for i, content in enumerate(bolds):
        text = text.replace(f"\x00BOLD{i}\x00", f"*{content}*")
    text = _re.sub(r"~~(.+?)~~", r"~\1~", text)
    text = _re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=_re.MULTILINE)
    text = _re.sub(r"^(\s*)[-*]\s+", r"\1• ", text, flags=_re.MULTILINE)

    for i, code in enumerate(inlines):
        text = text.replace(f"\x00INLINE{i}\x00", code)
    for i, block in enumerate(blocks):
        text = text.replace(f"\x00BLOCK{i}\x00", block)

    return text


_TG_ESCAPE_CHARS = _re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _tg_escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2 plain text."""
    return _TG_ESCAPE_CHARS.sub(r"\\\1", text)


def md_to_telegram(text: str) -> str:
    """Convert standard Markdown to Telegram MarkdownV2.

    Strategy: extract all formatting entities into placeholders, escape the
    remaining plain text, then restore the entities with correct MV2 syntax.
    """
    stash: List[str] = []

    def _put(mv2: str) -> str:
        stash.append(mv2)
        return f"\x00S{len(stash) - 1}\x00"

    # 1. Stash code blocks (``` ... ```) — content must NOT be escaped
    def _stash_codeblock(m: _re.Match) -> str:
        raw = m.group(0)
        # Extract lang hint and body
        inner = _re.sub(r"^```\w*\n?", "", raw)
        inner = _re.sub(r"\n?```$", "", inner)
        return _put(f"```\n{inner}\n```")

    text = _re.sub(r"```[\s\S]*?```", _stash_codeblock, text)

    # 2. Stash inline code — content must NOT be escaped
    def _stash_inline(m: _re.Match) -> str:
        return _put(m.group(0))

    text = _re.sub(r"`[^`]+`", _stash_inline, text)

    # 3. Remove image links ![text](url) — images are sent separately
    text = _re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", "", text)

    # 4. Stash links [text](url) — text gets escaped, url doesn't
    def _stash_link(m: _re.Match) -> str:
        return _put(f"[{_tg_escape(m.group(1))}]({m.group(2)})")

    text = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _stash_link, text)

    # 4. Stash bold **text** → *escaped_text*
    def _stash_bold(m: _re.Match) -> str:
        return _put(f"*{_tg_escape(m.group(1))}*")

    text = _re.sub(r"\*\*(.+?)\*\*", _stash_bold, text)

    # 5. Stash italic *text* → _escaped\_text_
    def _stash_italic(m: _re.Match) -> str:
        return _put(f"_{_tg_escape(m.group(1))}_")

    text = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", _stash_italic, text)

    # 6. Stash strikethrough ~~text~~ → ~escaped_text~
    def _stash_strike(m: _re.Match) -> str:
        return _put(f"~{_tg_escape(m.group(1))}~")

    text = _re.sub(r"~~(.+?)~~", _stash_strike, text)

    # 7. Headers → bold
    def _stash_header(m: _re.Match) -> str:
        return _put(f"*{_tg_escape(m.group(1))}*")

    text = _re.sub(r"^#{1,6}\s+(.+)$", _stash_header, text, flags=_re.MULTILINE)

    # 8. Lists: - item → • item (done after escaping below)
    text = _re.sub(r"^(\s*)[-]\s+", r"\1• ", text, flags=_re.MULTILINE)

    # 9. Escape all remaining plain text
    text = _tg_escape(text)

    # 10. Restore stashed entities
    for i, entity in enumerate(stash):
        text = text.replace(f"\\x00S{i}\\x00", entity)
        text = text.replace(f"\x00S{i}\x00", entity)

    return text


def md_to_plain(text: str) -> str:
    """Strip Markdown to plain text for channels that don't support markup."""
    blocks: List[str] = []

    def _stash_block(m: _re.Match) -> str:
        # Keep the code content but strip the fences
        content = m.group(0)
        content = _re.sub(r"^```\w*\n?", "", content)
        content = _re.sub(r"\n?```$", "", content)
        blocks.append(content)
        return f"\x00BLOCK{len(blocks) - 1}\x00"

    text = _re.sub(r"```[\s\S]*?```", _stash_block, text)

    text = _re.sub(r"`([^`]+)`", r"\1", text)
    text = _re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", "", text)  # Remove image links (sent separately)
    text = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = _re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = _re.sub(r"__(.+?)__", r"\1", text)
    text = _re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text)
    text = _re.sub(r"~~(.+?)~~", r"\1", text)
    text = _re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=_re.MULTILINE)
    text = _re.sub(r"^(\s*)[-*]\s+", r"\1• ", text, flags=_re.MULTILINE)

    for i, block in enumerate(blocks):
        text = text.replace(f"\x00BLOCK{i}\x00", block)

    return text


# ─── Image helpers ───────────────────────────────────────────────────────────

_DATA_URI_RE = r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+"

#image bytes into a string you can embed anywhere
def bytes_to_data_uri(data: bytes, filename: str = "") -> str:
    """Convert raw image bytes to a ``data:image/...;base64,...`` URI.

    Returns an empty string if *data* exceeds the configured size limit.
    """
    from pantheon.utils.image_detection import _get_image_limits
    max_size, _ = _get_image_limits()
    if len(data) > max_size:
        logger.warning("Image too large (%d bytes, limit %d), skipping", len(data), max_size)
        return ""
    mime, _ = mimetypes.guess_type(filename or "image.png")
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def data_uri_to_bytes(uri: str) -> tuple[bytes, str]:
    """Convert a data URI back to ``(raw_bytes, mime_type)``.

    Returns ``(b"", "")`` when the URI is not a valid base64 data URI.
    """
    if not uri or not uri.startswith("data:"):
        return b"", ""
    try:
        header, payload = uri.split(",", 1)
        mime = header.split(";")[0].replace("data:", "")
        return base64.b64decode(payload), mime
    except Exception:
        return b"", ""


def extract_images_from_result(result: Dict[str, Any]) -> List[str]:
    """Pull base64 data-URIs from a chatroom response.

    Scans ``result["messages"]`` for tool-result messages that carry a
    ``base64_uri`` field in their ``raw_content``.
    """
    uris: List[str] = []
    for msg in result.get("messages") or []:
        raw = msg.get("raw_content")
        if isinstance(raw, dict):
            uri_val = raw.get("base64_uri")
            if isinstance(uri_val, list):
                uris.extend(u for u in uri_val if isinstance(u, str) and u)
            elif isinstance(uri_val, str) and uri_val:
                uris.append(uri_val)
    return uris


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
        triggers a UI refresh via *on_update*.

        Only appends chunks from the primary (first seen) agent; sub-agent
        streaming is suppressed to avoid confusing intermediate output.
        """
        _primary: List[str] = []

        async def _on_chunk(chunk: Dict[str, Any]) -> None:
            agent_name = chunk.get("agent_name", "")
            if agent_name:
                if not _primary:
                    _primary.append(agent_name)
                elif agent_name != _primary[0]:
                    return  # suppress sub-agent chunks

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
        - Updates *llm_buf* with assistant text or notify_user messages
        - Calls *progress_cb(label)* for tool/transfer events
        - Calls *refresh_cb()* to push updates to the channel UI
        """
        _primary_agent: List[str] = []  # tracks the main (first) agent name

        async def _on_step(step: Dict[str, Any]) -> None:
            # Only show assistant text from the primary (leader) agent
            txt = parse_step_text(step)
            if txt:
                agent_name = step.get("agent_name", "")
                if not _primary_agent:
                    _primary_agent.append(agent_name)
                if agent_name == _primary_agent[0] or not _primary_agent[0]:
                    llm_buf.clear()
                    llm_buf.append(txt)

            # Capture notify_user / task completion messages
            if step.get("role") == "tool" and not step.get("transfer"):
                raw = step.get("raw_content")
                if isinstance(raw, dict):
                    notify_msg = raw.get("message")
                    if isinstance(notify_msg, str) and notify_msg.strip():
                        llm_buf.clear()
                        llm_buf.append(notify_msg)

            # Show tool/agent-transfer progress
            progress = parse_step_progress(step)
            if progress is not None and progress_cb is not None:
                await progress_cb(progress)

            if refresh_cb is not None:
                await refresh_cb()

        return _on_step

    def make_image_step_callback(
        self,
        llm_buf: List[str],
        image_buf: List[str],
        file_buf: Optional[List[str]] = None,
        progress_cb: Optional[Callable[[str], Coroutine]] = None,
        refresh_cb: Optional[Callable[[], Coroutine]] = None,
    ) -> Callable[[Dict[str, Any]], Coroutine]:
        """Like ``make_step_callback`` but also collects images and file paths
        from tool results.

        *image_buf* receives data-URI strings for images.
        *file_buf* (if provided) receives absolute file paths from notify_user
        ``paths`` fields for non-image attachments (PDF, markdown, etc.).
        Also captures notify_user / task completion messages into *llm_buf*.
        """
        _file_buf = file_buf if file_buf is not None else []
        _primary_agent: List[str] = []  # tracks the main (first) agent name
        _in_sub_agent: List[bool] = [False]  # mutable flag for sub-agent state

        async def _on_step(step: Dict[str, Any]) -> None:
            # Track sub-agent state via agent_name changes
            agent_name = step.get("agent_name", "")
            if agent_name:
                if not _primary_agent:
                    _primary_agent.append(agent_name)
                _in_sub_agent[0] = agent_name != _primary_agent[0]

            # Only show assistant text from the primary (leader) agent.
            # Sub-agent intermediate responses are ignored.
            txt = parse_step_text(step)
            if txt and not _in_sub_agent[0]:
                llm_buf.clear()
                llm_buf.append(txt)

            # Collect images, files, and notify_user messages from tool results
            if step.get("role") == "tool" and not step.get("transfer"):
                raw = step.get("raw_content")
                if isinstance(raw, dict):
                    # Collect images
                    uri_val = raw.get("base64_uri")
                    if isinstance(uri_val, list):
                        image_buf.extend(
                            u for u in uri_val
                            if isinstance(u, str) and u and u not in image_buf
                        )
                    elif isinstance(uri_val, str) and uri_val and uri_val not in image_buf:
                        image_buf.append(uri_val)

                    # Collect file paths from notify_user results
                    paths = raw.get("paths")
                    if isinstance(paths, list):
                        import os
                        for p in paths:
                            if isinstance(p, str) and p not in _file_buf and os.path.isfile(p):
                                _file_buf.append(p)

                    # Capture notify_user / task completion message
                    notify_msg = raw.get("message")
                    if isinstance(notify_msg, str) and notify_msg.strip():
                        llm_buf.clear()
                        llm_buf.append(notify_msg)

            progress = parse_step_progress(step)
            if progress is not None and progress_cb is not None:
                await progress_cb(progress)

            if refresh_cb is not None:
                await refresh_cb()

        return _on_step
