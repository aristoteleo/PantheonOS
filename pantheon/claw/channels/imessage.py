from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import threading
from dataclasses import dataclass
from typing import Any

from pantheon.claw.registry import ConversationRoute
from pantheon.claw.runtime import ChannelRuntime, text_chunks

logger = logging.getLogger("pantheon.claw.channels.imessage")

_MAX_TEXT = 3800
_IMSG_PERMISSION_DENIED_RE = re.compile(
    r'permissionDenied\(path:\s*"(?P<path>[^"]+)"(?:,\s*underlying:\s*(?P<detail>.+?))?\)$',
    re.IGNORECASE,
)


@dataclass
class IMessageTarget:
    kind: str
    value: str
    service: str = "auto"


def _normalize_handle(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    lower = value.lower()
    for prefix in ("imessage:", "sms:", "auto:"):
        if lower.startswith(prefix):
            return _normalize_handle(value[len(prefix):])
    if value.startswith("+"):
        return re.sub(r"\s+", "", value)
    if "@" in value:
        return value.lower()
    return re.sub(r"\s+", "", value)


def _parse_target(raw: str) -> IMessageTarget:
    text = (raw or "").strip()
    if not text:
        raise ValueError("iMessage target is required")
    lower = text.lower()
    for prefix, service in (("imessage:", "imessage"), ("sms:", "sms"), ("auto:", "auto")):
        if lower.startswith(prefix):
            nested = _parse_target(text[len(prefix):].strip())
            if nested.kind == "handle":
                nested.service = service
            return nested
    if lower.startswith("chat_id:"):
        return IMessageTarget(kind="chat_id", value=str(int(text.split(":", 1)[1].strip())))
    if lower.startswith("chat_guid:"):
        return IMessageTarget(kind="chat_guid", value=text.split(":", 1)[1].strip())
    if lower.startswith("chat_identifier:"):
        return IMessageTarget(kind="chat_identifier", value=text.split(":", 1)[1].strip())
    return IMessageTarget(kind="handle", value=text, service="auto")


def _message_target(message: dict[str, Any]) -> str | None:
    chat_id = message.get("chat_id")
    if isinstance(chat_id, int):
        return f"chat_id:{chat_id}"
    chat_guid = str(message.get("chat_guid") or "").strip()
    if chat_guid:
        return f"chat_guid:{chat_guid}"
    chat_identifier = str(message.get("chat_identifier") or "").strip()
    if chat_identifier:
        return f"chat_identifier:{chat_identifier}"
    sender = _normalize_handle(str(message.get("sender") or ""))
    return sender or None


def _resolve_cli_path(cli_path: str) -> str | None:
    value = str(cli_path or "imsg").strip()
    if not value:
        return None
    expanded = os.path.expanduser(value)
    if os.path.sep in expanded:
        return expanded if os.path.isfile(expanded) and os.access(expanded, os.X_OK) else None
    return shutil.which(expanded)


async def _probe_rpc_support(cli_path: str, timeout: float) -> None:
    resolved = _resolve_cli_path(cli_path)
    if resolved is None:
        raise RuntimeError(f"Unable to find imsg executable at {cli_path}")

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved,
            "rpc",
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Unable to launch imsg at {cli_path}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("imsg rpc --help timed out")

    combined = "\n".join(
        part.decode("utf-8", errors="ignore").strip()
        for part in (stdout, stderr)
        if part
    ).strip().lower()
    if "unknown command" in combined and "rpc" in combined:
        raise RuntimeError("The installed imsg does not support the rpc subcommand")
    if proc.returncode not in (0, None):
        raise RuntimeError(combined or f"imsg rpc --help failed with code {proc.returncode}")


class IMessageRpcClient:
    def __init__(
        self,
        *,
        cli_path: str = "imsg",
        db_path: str | None = None,
        on_notification=None,
    ) -> None:
        self._cli_path = cli_path or "imsg"
        self._db_path = os.path.expanduser(db_path) if db_path else None
        self._on_notification = on_notification
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._writer_lock = asyncio.Lock()
        self._next_id = 1
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._terminal_error: RuntimeError | None = None

    async def start(self) -> None:
        if self._proc is not None:
            return
        args = [self._cli_path, "rpc"]
        if self._db_path:
            args.extend(["--db", self._db_path])
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Unable to launch imsg at {self._cli_path}") from exc
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.stdin:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            proc.terminate()
        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self._fail_all(self._terminal_error or RuntimeError("imsg rpc closed"))

    async def wait_closed(self) -> None:
        if self._proc is not None:
            await self._proc.wait()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        await self.start()
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("imsg rpc not running")
        request_id = str(self._next_id)
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {
            "jsonrpc": "2.0",
            "id": int(request_id),
            "method": method,
            "params": params or {},
        }
        async with self._writer_lock:
            self._proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def send_message(self, target: str, text: str) -> Any:
        parsed = _parse_target(target)
        params: dict[str, Any] = {"text": text, "service": parsed.service or "auto", "region": "US"}
        if parsed.kind == "chat_id":
            params["chat_id"] = int(parsed.value)
        elif parsed.kind == "chat_guid":
            params["chat_guid"] = parsed.value
        elif parsed.kind == "chat_identifier":
            params["chat_identifier"] = parsed.value
        else:
            params["to"] = parsed.value
        return await self.request("send", params, timeout=60.0)

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    parsed_error = self._parse_terminal_error(line)
                    if parsed_error is not None:
                        self._terminal_error = parsed_error
                        self._fail_all(parsed_error)
                    continue
                if payload.get("id") is not None:
                    request_id = str(payload["id"])
                    future = self._pending.get(request_id)
                    if future is None or future.done():
                        continue
                    if payload.get("error"):
                        error = payload["error"] or {}
                        message = str(error.get("message") or "imsg rpc error")
                        data = error.get("data")
                        if data is not None:
                            message = f"{message}: {data}"
                        future.set_exception(RuntimeError(message))
                    else:
                        future.set_result(payload.get("result"))
                    continue
                method = str(payload.get("method") or "").strip()
                params = payload.get("params")
                if method and isinstance(params, dict) and self._on_notification is not None:
                    await self._on_notification(method, params)
        finally:
            self._fail_all(self._terminal_error or RuntimeError("imsg rpc closed"))

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            parsed_error = self._parse_terminal_error(line)
            if parsed_error is not None:
                self._terminal_error = parsed_error
                self._fail_all(parsed_error)
            logger.warning("imsg rpc: %s", line)

    def _fail_all(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    @staticmethod
    def _parse_terminal_error(line: str) -> RuntimeError | None:
        text = str(line or "").strip()
        if not text:
            return None
        match = _IMSG_PERMISSION_DENIED_RE.search(text)
        if match:
            denied_path = match.group("path") or "~/Library/Messages/chat.db"
            detail = str(match.group("detail") or "authorization denied").strip()
            return RuntimeError(
                f"imsg cannot access the Messages database at {denied_path}. "
                f"macOS denied permission ({detail}). Grant Full Disk Access and retry."
            )
        return None


class IMessageGatewayBot(ChannelRuntime):
    def __init__(self, *, bridge: Any, config: dict[str, Any], stop_event: threading.Event) -> None:
        super().__init__(bridge=bridge)
        self._cli_path = str(config.get("cli_path") or "imsg")
        self._db_path = str(config.get("db_path") or "").strip() or None
        self._include_attachments = bool(config.get("include_attachments"))
        self._stop_event = stop_event
        self._targets: dict[str, str] = {}
        self._client = IMessageRpcClient(
            cli_path=self._cli_path,
            db_path=self._db_path,
            on_notification=self._handle_notification,
        )

    async def run(self) -> None:
        logger.info("iMessage probing imsg RPC support (cli=%s)", self._cli_path)
        await _probe_rpc_support(self._cli_path, timeout=10.0)
        logger.info("iMessage starting RPC client")
        await self._client.start()
        await self._client.request(
            "watch.subscribe",
            {"attachments": bool(self._include_attachments)},
            timeout=60.0,
        )
        try:
            if self._stop_event.is_set():
                return
            wait_closed_task = asyncio.create_task(self._client.wait_closed())
            stop_task = asyncio.create_task(asyncio.to_thread(self._stop_event.wait))
            done, pending = await asyncio.wait(
                {wait_closed_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if stop_task in done and self._stop_event.is_set():
                await self._client.stop()
            elif wait_closed_task in done:
                await wait_closed_task
        finally:
            await self._client.stop()

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method != "message":
            if method == "error":
                logger.warning("imessage watch error: %s", params)
            return
        message = params.get("message")
        if isinstance(message, dict):
            await self._handle_message(message)

    def _route_for_message(self, message: dict[str, Any]) -> ConversationRoute | None:
        is_group = bool(message.get("is_group"))
        chat_id = message.get("chat_id")
        sender = _normalize_handle(str(message.get("sender") or ""))
        chat_identifier = str(message.get("chat_identifier") or "").strip()
        scope_id = ""
        if isinstance(chat_id, int):
            scope_id = str(chat_id)
        elif not is_group and sender:
            scope_id = sender
        elif chat_identifier:
            scope_id = chat_identifier
        if not scope_id:
            return None
        return ConversationRoute(
            channel="imessage",
            scope_type="group" if is_group else "dm",
            scope_id=scope_id,
            sender_id=sender or None,
        )

    def _command_parts(self, text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "", text
        parts = text.split(maxsplit=1)
        return parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""

    async def _send_text(self, target: str, text: str) -> None:
        for chunk in text_chunks(text, limit=_MAX_TEXT):
            await self._client.send_message(target, chunk)

    async def _handle_command(self, route: ConversationRoute, target: str, text: str) -> bool:
        result = await self._bridge.handle_control_command(route, text)
        if not result.get("handled"):
            return False
        if result.get("clear_pending"):
            self._clear_pending(route.route_key())
        await self._send_text(target, result.get("message") or "")
        return True

    async def _analysis_wrapper(self, route: ConversationRoute, target: str, user_text: str) -> None:
        route_key = route.route_key()
        llm_buf: list[str] = []
        await self._send_text(target, "Thinking...")

        # iMessage has no message-edit API — use callbacks for correct buffer assembly
        on_chunk = self.make_chunk_callback(llm_buf)
        on_step = self.make_step_callback(llm_buf)

        try:
            result = await self._bridge.run_chat(
                route,
                user_text,
                process_chunk=on_chunk,
                process_step_message=on_step,
            )
            final_text = str(result.get("response") or "".join(llm_buf) or "Done.")
            await self._send_text(target, final_text)
        except asyncio.CancelledError:
            await self._send_text(target, "Cancelled.")
            raise
        except Exception as exc:
            logger.exception("iMessage analysis failed")
            await self._send_text(target, f"Error: {exc}")
        finally:
            self._pop_task(route_key)
            queued = self._pop_queued(route_key)
            if queued:
                next_text = "\n\n".join(queued)
                task = asyncio.create_task(self._analysis_wrapper(route, target, next_text))
                self._set_task(route_key, task, next_text)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        sender = _normalize_handle(str(message.get("sender") or ""))
        if not sender or bool(message.get("is_from_me")):
            return
        route = self._route_for_message(message)
        target = _message_target(message)
        if route is None or not target:
            return
        text = str(message.get("text") or "").strip()
        if not text and message.get("attachments") and self._include_attachments:
            text = "<attachment>"
        if not text:
            return
        if text.startswith("/") and await self._handle_command(route, target, text):
            return
        route_key = route.route_key()
        self._targets[route_key] = target
        if self._get_running(route_key) is not None:
            self._queue_message(route_key, text)
            await self._send_text(target, "Queued after current analysis.")
            return
        task = asyncio.create_task(self._analysis_wrapper(route, target, text))
        self._set_task(route_key, task, text)


async def run_imessage_channel(*, bridge: Any, config: dict[str, Any], stop_event: threading.Event) -> None:
    bot = IMessageGatewayBot(bridge=bridge, config=config, stop_event=stop_event)
    await bot.run()
