from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

import requests

from pantheon.claw.registry import ConversationRoute
from pantheon.claw.runtime import ChannelRuntime, Deduper, text_chunks

logger = logging.getLogger("pantheon.claw.channels.feishu")

_MAX_TEXT = 4800
_MAX_BODY_BYTES = 2 * 1024 * 1024
_FEISHU_EVENT_MESSAGE_RECEIVE = "im.message.receive_v1"
_EDIT_GAP_SECONDS = 1.2


def _json_loads_safely(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    def _tenant_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._expires_at - 60:
            return self._token
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed: {data}")
        self._token = data["tenant_access_token"]
        self._expires_at = now + int(data.get("expire", 7200))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tenant_access_token()}"}

    def send_text(self, chat_id: str, text: str) -> str | None:
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text[:_MAX_TEXT]}, ensure_ascii=False),
        }
        response = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers=self._headers(),
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu send text failed: {data}")
        return ((data.get("data") or {}).get("message_id"))

    def edit_text(self, message_id: str, text: str) -> bool:
        payload = {
            "msg_type": "text",
            "content": json.dumps({"text": text[:_MAX_TEXT]}, ensure_ascii=False),
        }
        response = requests.patch(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
            headers=self._headers(),
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            return False
        data = response.json()
        return data.get("code") == 0

    def download_file(self, file_key: str) -> bytes:
        response = requests.get(
            f"https://open.feishu.cn/open-apis/im/v1/files/{file_key}/download",
            headers=self._headers(),
            timeout=120,
        )
        response.raise_for_status()
        return response.content


class FeishuWebhookSecurity:
    def __init__(
        self,
        *,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
    ) -> None:
        self._verification_token = (verification_token or "").strip()
        self._encrypt_key = (encrypt_key or "").strip()

    @staticmethod
    def _canonical_json(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _sha_digest(text: str, algo: str) -> str:
        hasher = hashlib.new(algo)
        hasher.update(text.encode("utf-8"))
        return hasher.hexdigest()

    def verify_signature(self, headers: Mapping[str, str], raw_body: str, parsed_body: Any) -> bool:
        if not self._encrypt_key:
            return True
        timestamp = (headers.get("x-lark-request-timestamp") or "").strip()
        nonce = (headers.get("x-lark-request-nonce") or "").strip()
        signature = (headers.get("x-lark-signature") or "").strip().lower()
        if not timestamp or not nonce or not signature:
            return False
        canonical = self._canonical_json(parsed_body)
        expected_canonical = self._sha_digest(
            f"{timestamp}{nonce}{self._encrypt_key}{canonical}",
            "sha256",
        )
        if signature == expected_canonical:
            return True
        expected_raw = self._sha_digest(
            f"{timestamp}{nonce}{self._encrypt_key}{raw_body}",
            "sha256",
        )
        return signature == expected_raw

    def verify_token(self, payload: Any) -> bool:
        if not self._verification_token:
            return True
        token = str((payload or {}).get("token") or "").strip()
        return token == self._verification_token

    def decrypt_payload(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise RuntimeError("Feishu payload must be a JSON object")
        encrypt = body.get("encrypt")
        if not self._encrypt_key or not encrypt:
            return body
        key = self._encrypt_key.encode("utf-8")
        encrypted = base64.b64decode(str(encrypt))
        iv = key[:16]
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        data = unpadder.update(padded) + unpadder.finalize()
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Feishu encrypted payload is not an object")
        return payload


class FeishuRuntime(ChannelRuntime):
    def __init__(self, *, bridge: Any, client: FeishuClient) -> None:
        super().__init__(bridge=bridge)
        self._client = client
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="pantheon-claw-feishu")
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=1.0)

    @staticmethod
    def _route(chat_id: str, thread_id: str | None, chat_type: str | None, sender_id: str | None = None) -> ConversationRoute:
        norm = (chat_type or "").strip().lower()
        if norm in {"p2p", "private", "direct", "dm"}:
            scope_type = "dm"
        elif norm in {"group", "chat", "room"}:
            scope_type = "group"
        else:
            scope_type = "chat"
        return ConversationRoute(
            channel="feishu",
            scope_type=scope_type,
            scope_id=str(chat_id),
            thread_id=(str(thread_id) if thread_id else None),
            sender_id=sender_id,
        )

    def _command_parts(self, text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "", text
        parts = text.split(maxsplit=1)
        return parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""

    async def _send_text(self, chat_id: str, text: str) -> str | None:
        message_id = None
        for chunk in text_chunks(text, limit=_MAX_TEXT):
            message_id = await asyncio.to_thread(self._client.send_text, chat_id, chunk)
        return message_id

    async def _analysis_wrapper(self, route: ConversationRoute, chat_id: str, user_text: str) -> None:
        route_key = route.route_key()
        llm_buf: list[str] = []
        last_progress = ""
        draft_id = await self._send_text(chat_id, "Thinking...")
        last_edit = 0.0

        async def refresh(force: bool = False) -> None:
            nonlocal last_edit
            if not draft_id:
                return
            now = time.monotonic()
            if not force and (now - last_edit) < _EDIT_GAP_SECONDS:
                return
            preview = "".join(llm_buf).strip() or last_progress or "Thinking..."
            ok = await asyncio.to_thread(self._client.edit_text, draft_id, preview[-_MAX_TEXT:])
            if ok:
                last_edit = now

        async def _set_progress(label: str) -> None:
            nonlocal last_progress
            last_progress = label

        on_chunk = self.make_chunk_callback(llm_buf, on_update=lambda: refresh(False))
        on_step = self.make_step_callback(
            llm_buf,
            progress_cb=_set_progress,
            refresh_cb=lambda: refresh(True),
        )

        try:
            result = await self._bridge.run_chat(
                route,
                user_text,
                process_chunk=on_chunk,
                process_step_message=on_step,
            )
            final_text = str(result.get("response") or "".join(llm_buf) or "Done.")
            if draft_id and not await asyncio.to_thread(self._client.edit_text, draft_id, final_text[-_MAX_TEXT:]):
                await self._send_text(chat_id, final_text)
        except asyncio.CancelledError:
            if draft_id and not await asyncio.to_thread(self._client.edit_text, draft_id, "Cancelled."):
                await self._send_text(chat_id, "Cancelled.")
            raise
        except Exception as exc:
            logger.exception("Feishu analysis failed")
            error_text = f"Error: {exc}"
            if draft_id and not await asyncio.to_thread(self._client.edit_text, draft_id, error_text):
                await self._send_text(chat_id, error_text)
        finally:
            self._pop_task(route_key)
            queued = self._pop_queued(route_key)
            if queued:
                next_text = "\n\n".join(queued)
                task = asyncio.create_task(self._analysis_wrapper(route, chat_id, next_text))
                self._set_task(route_key, task, next_text)

    async def handle_text(
        self,
        chat_id: str,
        thread_id: str | None,
        text: str,
        *,
        chat_type: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        text = (text or "").strip()
        if not text:
            return
        route = self._route(chat_id, thread_id, chat_type, sender_id)
        cmd, tail = self._command_parts(text)
        if cmd:
            result = await self._bridge.handle_control_command(route, text)
            if result.get("handled"):
                if result.get("clear_pending"):
                    self._clear_pending(route.route_key())
                await self._send_text(chat_id, result.get("message") or "")
                return

        route_key = route.route_key()
        if self._get_running(route_key) is not None:
            self._queue_message(route_key, tail or text)
            await self._send_text(chat_id, "Queued after current analysis.")
            return
        task = asyncio.create_task(self._analysis_wrapper(route, chat_id, tail or text))
        self._set_task(route_key, task, tail or text)

    async def handle_file(
        self,
        chat_id: str,
        thread_id: str | None,
        file_key: str,
        file_name: str,
        *,
        chat_type: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        route = self._route(chat_id, thread_id, chat_type, sender_id)
        note = (
            f"Received file `{Path(file_name or 'upload.bin').name}`. "
            "PantheonClaw currently routes conversational analysis here; upload handling is not wired into the chat workspace yet."
        )
        await self._send_text(chat_id, note)
        status = await self._bridge.get_route_status(route)
        if not status["mapped"]:
            await self._send_text(chat_id, "Send a text instruction first to create the routed chat.")


class FeishuWebhookProcessor:
    def __init__(
        self,
        *,
        runtime: FeishuRuntime,
        path: str,
        deduper: Deduper,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        max_body_bytes: int = _MAX_BODY_BYTES,
    ) -> None:
        self._runtime = runtime
        self._path = path
        self._deduper = deduper
        self._security = FeishuWebhookSecurity(
            verification_token=verification_token,
            encrypt_key=encrypt_key,
        )
        self._max_body_bytes = max(1024, int(max_body_bytes))

    @staticmethod
    def _as_json(data: Any) -> bytes:
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def process_http(
        self,
        request_path: str,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        if request_path.split("?", 1)[0] != self._path:
            return 404, {}, b""
        content_type = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
        if "application/json" not in content_type:
            return 415, {"Content-Type": "text/plain; charset=utf-8"}, b"Unsupported Media Type"
        if len(raw_body) > self._max_body_bytes:
            return 413, {"Content-Type": "text/plain; charset=utf-8"}, b"Payload Too Large"
        raw_text = raw_body.decode("utf-8", errors="replace") if raw_body else "{}"
        try:
            body = json.loads(raw_text)
        except Exception:
            return 400, {}, b""
        if not self._security.verify_signature(headers, raw_text, body):
            return 401, {"Content-Type": "text/plain; charset=utf-8"}, b"Invalid Signature"
        try:
            payload = self._security.decrypt_payload(body)
        except Exception as exc:
            logger.warning("Failed to decrypt Feishu payload: %s", exc)
            return 400, {"Content-Type": "text/plain; charset=utf-8"}, b"Invalid Encrypted Payload"
        if not self._security.verify_token(payload):
            return 403, {"Content-Type": "text/plain; charset=utf-8"}, b"Invalid Verification Token"
        if (payload.get("type") or "").strip() == "url_verification":
            return 200, {"Content-Type": "application/json"}, self._as_json({"challenge": payload.get("challenge", "")})
        _process_feishu_event_payload(
            runtime=self._runtime,
            deduper=self._deduper,
            payload=payload,
            expected_event_type=_FEISHU_EVENT_MESSAGE_RECEIVE,
        )
        return 200, {}, b""


def _process_feishu_event_payload(
    *,
    runtime: FeishuRuntime,
    deduper: Deduper,
    payload: Any,
    expected_event_type: str = _FEISHU_EVENT_MESSAGE_RECEIVE,
) -> bool:
    if not isinstance(payload, dict):
        return False
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    event_type = (header.get("event_type") or (event or {}).get("type") or "").strip()
    if event_type != expected_event_type:
        return False
    event_id = (header.get("event_id") or "").strip()
    if event_id and deduper.seen_or_record(f"event:{event_id}"):
        return True
    if not isinstance(event, dict):
        return True
    msg = event.get("message") or {}
    if not isinstance(msg, dict):
        return True
    message_id = (msg.get("message_id") or "").strip()
    if message_id and deduper.seen_or_record(f"message:{message_id}"):
        return True
    message_type = (msg.get("message_type") or "").strip().lower()
    chat_id = (event.get("chat_id") or msg.get("chat_id") or "").strip()
    chat_type = (
        event.get("chat_type")
        or msg.get("chat_type")
        or event.get("conversation_type")
        or msg.get("conversation_type")
    )
    thread_id = (
        event.get("root_id")
        or msg.get("root_id")
        or event.get("thread_id")
        or msg.get("thread_id")
    )
    sender_id = str(((event.get("sender") or {}).get("sender_id") or {}).get("open_id") or "").strip() or None
    if not chat_id:
        return True
    content = _json_loads_safely(msg.get("content"))
    if message_type == "text":
        text = (content.get("text") or "").strip()
        if text:
            runtime.submit(runtime.handle_text(chat_id, thread_id, text, chat_type=chat_type, sender_id=sender_id))
    elif message_type in {"file", "media"}:
        file_key = (content.get("file_key") or "").strip()
        file_name = (content.get("file_name") or content.get("name") or "upload.bin").strip()
        if file_key:
            runtime.submit(
                runtime.handle_file(
                    chat_id,
                    thread_id,
                    file_key,
                    file_name,
                    chat_type=chat_type,
                    sender_id=sender_id,
                )
            )
    elif message_type == "image":
        runtime.submit(
            runtime.handle_text(
                chat_id,
                thread_id,
                "Received an image. Send a text instruction to continue the routed analysis.",
                chat_type=chat_type,
                sender_id=sender_id,
            )
        )
    return True


async def run_feishu_channel(*, bridge: Any, config: dict[str, Any], stop_event: threading.Event) -> None:
    client = FeishuClient(str(config.get("app_id") or ""), str(config.get("app_secret") or ""))
    runtime = FeishuRuntime(bridge=bridge, client=client)
    deduper = Deduper.for_channel("feishu")
    mode = str(config.get("connection_mode") or "websocket").strip().lower()
    logger.info("Feishu channel starting (mode=%s app_id=%s)", mode, config.get("app_id"))
    try:
        if mode == "webhook":
            path = str(config.get("path") or "/feishu/events")
            processor = FeishuWebhookProcessor(
                runtime=runtime,
                path=path,
                deduper=deduper,
                verification_token=config.get("verification_token"),
                encrypt_key=config.get("encrypt_key"),
            )

            class FeishuHandler(BaseHTTPRequestHandler):
                def do_POST(self) -> None:  # noqa: N802
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length else b"{}"
                    status, extra_headers, payload = processor.process_http(self.path, self.headers, raw)
                    self.send_response(status)
                    for key, value in extra_headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    if payload:
                        self.wfile.write(payload)

                def log_message(self, fmt: str, *args: object) -> None:
                    logger.debug("feishu_http " + fmt, *args)

            host = str(config.get("host") or "0.0.0.0")
            port = int(config.get("port") or 8080)
            server = ThreadingHTTPServer((host, port), FeishuHandler)

            def stop_server() -> None:
                stop_event.wait()
                try:
                    server.shutdown()
                except Exception:
                    pass

            threading.Thread(target=stop_server, daemon=True, name="pantheon-claw-feishu-stop").start()
            try:
                await asyncio.to_thread(server.serve_forever)
            finally:
                server.server_close()
        else:
            try:
                import lark_oapi as lark
            except ImportError as exc:
                raise RuntimeError(
                    "Feishu websocket mode requires 'lark-oapi'. Install pantheon-agents[claw]."
                ) from exc

            def on_message(data: Any) -> None:
                try:
                    raw = lark.JSON.marshal(data)
                    payload = json.loads(raw) if isinstance(raw, str) else {}
                    _process_feishu_event_payload(
                        runtime=runtime,
                        deduper=deduper,
                        payload=payload,
                        expected_event_type=_FEISHU_EVENT_MESSAGE_RECEIVE,
                    )
                except Exception:
                    logger.exception("Feishu websocket event handling failed")

            app_id = str(config.get("app_id") or "")
            app_secret = str(config.get("app_secret") or "")
            encrypt_key = str(config.get("encrypt_key") or "")
            verification_token = str(config.get("verification_token") or "")

            # ws_client must be created INSIDE the plain thread so that lark_oapi
            # never captures the running asyncio event loop.  If created here
            # (inside asyncio.run()), lark_oapi's Client.__init__ / start() calls
            # asyncio.get_event_loop(), gets the already-running loop, and then
            # loop.run_until_complete() raises "This event loop is already running".
            ws_done = threading.Event()
            ws_exc: list[BaseException] = []
            ws_holder: list[Any] = []  # populated by run_ws before start()

            def run_ws() -> None:
                # lark_oapi/ws/client.py captures `asyncio.get_event_loop()` at
                # module-import time as a module-level variable named `loop`.
                # If the module was first imported inside asyncio.run(), that
                # variable holds the already-running loop, so every subsequent
                # loop.run_until_complete() call raises "This event loop is
                # already running" — even from a plain thread.
                # Fix: overwrite the module-level loop here, from a plain thread
                # where no event loop is running, so lark_oapi gets a fresh loop.
                import lark_oapi.ws.client as _lark_ws_client
                _lark_ws_client.loop = asyncio.new_event_loop()

                builder = lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
                event_handler = builder.register_p2_im_message_receive_v1(on_message).build()
                _ws = lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.INFO,
                )
                ws_holder.append(_ws)
                try:
                    _ws.start()
                except Exception as exc:
                    logger.exception("Feishu WebSocket client crashed: %s", exc)
                    ws_exc.append(exc)
                finally:
                    ws_done.set()

            def stop_ws() -> None:
                stop_event.wait()
                if ws_holder:
                    try:
                        ws_holder[0]._auto_reconnect = False
                    except Exception:
                        pass
                    try:
                        asyncio.run(ws_holder[0]._disconnect())
                    except Exception:
                        pass

            ws_thread = threading.Thread(target=run_ws, daemon=True, name="pantheon-claw-feishu-ws")
            threading.Thread(target=stop_ws, daemon=True, name="pantheon-claw-feishu-ws-stop").start()
            ws_thread.start()

            stop_task = asyncio.create_task(asyncio.to_thread(stop_event.wait))
            done_task = asyncio.create_task(asyncio.to_thread(ws_done.wait))
            _, pending = await asyncio.wait({stop_task, done_task}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

            if ws_exc:
                raise ws_exc[0]
    finally:
        runtime.stop()
