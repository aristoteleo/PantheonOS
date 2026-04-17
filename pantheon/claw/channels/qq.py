from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from pantheon.claw.registry import ConversationRoute
from pantheon.claw.runtime import ChannelRuntime, Deduper, data_uri_to_bytes, bytes_to_data_uri, text_chunks, md_to_plain, extract_display_text

from pantheon.utils.log import logger

_API_BASE = "https://api.sgroup.qq.com"
_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_MAX_TEXT = 2000
_RECONNECT_DELAYS = [1, 2, 5, 10, 30, 60]

_INTENT_PUBLIC_GUILD_MESSAGES = 1 << 30
_INTENT_DIRECT_MESSAGE = 1 << 12
_INTENT_GROUP_AND_C2C = 1 << 25
_INTENT_FULL = _INTENT_PUBLIC_GUILD_MESSAGES | _INTENT_DIRECT_MESSAGE | _INTENT_GROUP_AND_C2C

_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RESUME = 6
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11


@dataclass
class QQTarget:
    kind: str  # "c2c" | "group" | "guild" | "dm"
    id: str

    def route_key(self) -> str:
        return f"qq:{self.kind}:{self.id}"


# ── QQ REST client ────────────────────────────────────────────────────────────

class QQClient:
    """QQ Bot REST API client with cached access token."""

    def __init__(self, app_id: str, client_secret: str) -> None:
        self._app_id = app_id
        self._client_secret = client_secret
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def _get_access_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < self._expires_at - 60:
                return self._token
            response = requests.post(
                _TOKEN_URL,
                json={"appId": self._app_id, "clientSecret": self._client_secret},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            if not token:
                raise RuntimeError(f"QQ auth failed: {data}")
            self._token = token
            self._expires_at = now + int(data.get("expires_in", 7200))
            return token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"QQBot {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def _api(self, method: str, path: str, **kwargs: Any) -> Any:
        response = requests.request(
            method,
            f"{_API_BASE}{path}",
            headers=self._headers(),
            timeout=20,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def get_gateway_url(self) -> str:
        data = self._api("GET", "/gateway")
        return data["url"]

    def send_c2c(
        self,
        openid: str,
        text: str,
        msg_id: Optional[str] = None,
        msg_seq: int = 1,
    ) -> Optional[str]:
        """Send a C2C (1-to-1) message. Falls back to proactive if reply fails."""
        body: Dict[str, Any] = {"content": text[:_MAX_TEXT], "msg_type": 0, "msg_seq": msg_seq}
        if msg_id:
            body["msg_id"] = msg_id
        url = f"/v2/users/{openid}/messages"
        try:
            data = self._api("POST", url, json=body)
            return data.get("id") or data.get("message_id")
        except requests.HTTPError:
            if msg_id:
                body.pop("msg_id", None)
                try:
                    data = self._api("POST", url, json=body)
                    return data.get("id") or data.get("message_id")
                except Exception as e2:
                    logger.warning("QQ c2c proactive send failed: %s", e2)
            else:
                raise
        except Exception as exc:
            logger.warning("QQ c2c send failed: %s", exc)
        return None

    def send_group(
        self,
        group_openid: str,
        text: str,
        msg_id: Optional[str] = None,
        msg_seq: int = 1,
    ) -> Optional[str]:
        """Send a group message. Falls back to proactive if reply fails."""
        body: Dict[str, Any] = {"content": text[:_MAX_TEXT], "msg_type": 0, "msg_seq": msg_seq}
        if msg_id:
            body["msg_id"] = msg_id
        url = f"/v2/groups/{group_openid}/messages"
        try:
            data = self._api("POST", url, json=body)
            return data.get("id") or data.get("message_id")
        except requests.HTTPError:
            if msg_id:
                body.pop("msg_id", None)
                try:
                    data = self._api("POST", url, json=body)
                    return data.get("id") or data.get("message_id")
                except Exception as e2:
                    logger.warning("QQ group proactive send failed: %s", e2)
            else:
                raise
        except Exception as exc:
            logger.warning("QQ group send failed: %s", exc)
        return None

    def send_guild(
        self,
        channel_id: str,
        text: str,
        msg_id: Optional[str] = None,
    ) -> Optional[str]:
        """Send a guild channel message."""
        body: Dict[str, Any] = {"content": text[:_MAX_TEXT]}
        if msg_id:
            body["msg_id"] = msg_id
        try:
            data = self._api("POST", f"/channels/{channel_id}/messages", json=body)
            return data.get("id") or data.get("message_id")
        except Exception as exc:
            logger.warning("QQ guild send failed: %s", exc)
            return None

    def send_text(
        self,
        target: QQTarget,
        text: str,
        msg_id: Optional[str] = None,
        msg_seq: int = 1,
    ) -> Optional[str]:
        """Send plain text to any target type."""
        if target.kind == "c2c":
            return self.send_c2c(target.id, text, msg_id=msg_id, msg_seq=msg_seq)
        elif target.kind == "group":
            return self.send_group(target.id, text, msg_id=msg_id, msg_seq=msg_seq)
        elif target.kind in {"guild", "dm"}:
            return self.send_guild(target.id, text, msg_id=msg_id)
        else:
            raise RuntimeError(f"Unsupported QQ target kind: {target.kind}")

    def upload_media(
        self,
        target: QQTarget,
        data: bytes,
        file_type: int = 1,
    ) -> Optional[str]:
        """Upload media (image) and return file_info for sending.

        file_type: 1=image, 2=video, 3=voice, 4=file
        Returns the file_info string needed for rich media messages, or None.
        """
        import base64 as _b64
        if target.kind == "c2c":
            url = f"/v2/users/{target.id}/files"
        elif target.kind == "group":
            url = f"/v2/groups/{target.id}/files"
        else:
            return None
        body = {
            "file_type": file_type,
            "file_data": _b64.b64encode(data).decode("ascii"),
            "srv_send_msg": False,
        }
        try:
            resp = self._api("POST", url, json=body)
            return resp.get("file_info") or resp.get("file_uuid")
        except Exception as exc:
            logger.warning("QQ media upload failed: %s", exc)
            return None

    def send_media_message(
        self,
        target: QQTarget,
        file_info: str,
        msg_id: Optional[str] = None,
        msg_seq: int = 1,
    ) -> Optional[str]:
        """Send a previously uploaded media file as a message."""
        body: Dict[str, Any] = {
            "content": "",
            "msg_type": 7,
            "media": {"file_info": file_info},
            "msg_seq": msg_seq,
        }
        if msg_id:
            body["msg_id"] = msg_id
        if target.kind == "c2c":
            url = f"/v2/users/{target.id}/messages"
        elif target.kind == "group":
            url = f"/v2/groups/{target.id}/messages"
        else:
            return None
        try:
            data = self._api("POST", url, json=body)
            return data.get("id") or data.get("message_id")
        except Exception as exc:
            logger.warning("QQ send media message failed: %s", exc)
            return None


# ── QQRuntime ─────────────────────────────────────────────────────────────────

class QQRuntime(ChannelRuntime):
    """Manages per-route task queuing and dispatches messages to the bridge."""

    def __init__(self, *, bridge: Any, client: QQClient) -> None:
        super().__init__(bridge=bridge)
        self._client = client
        # msg_seq tracking: QQ requires a unique incrementing seq per msg_id.
        # Without this, the second+ messages referencing the same msg_id are
        # silently dropped by QQ.
        self._msg_seqs: Dict[str, int] = {}
        self._deduper = Deduper.for_channel("qq")
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="pantheon-claw-qq")
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Any) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=1.0)

    def _alloc_seq(self, msg_id: Optional[str]) -> int:
        """Return the next msg_seq for this msg_id (unique incrementing per QQ spec).

        QQ requires every message referencing the same msg_id to carry a unique,
        monotonically-increasing msg_seq. Without this the API silently drops
        the second and subsequent replies.
        """
        if not msg_id:
            return 1
        seq = self._msg_seqs.get(msg_id, 0) + 1
        self._msg_seqs[msg_id] = seq
        # Prevent unbounded growth (keep at most 500 tracked msg_ids)
        if len(self._msg_seqs) > 500:
            for k in list(self._msg_seqs.keys())[:100]:
                del self._msg_seqs[k]
        return seq

    def _send_text(self, target: QQTarget, text: str, msg_id: Optional[str] = None) -> Optional[str]:
        """Synchronous send with automatic msg_seq allocation."""
        seq = self._alloc_seq(msg_id)
        return self._client.send_text(target, text, msg_id=msg_id, msg_seq=seq)

    def _send_image(self, target: QQTarget, data_uri: str, msg_id: Optional[str] = None) -> None:
        """Upload and send an image to a QQ target."""
        raw, _mime = data_uri_to_bytes(data_uri)
        if not raw:
            return
        file_info = self._client.upload_media(target, raw, file_type=1)
        if file_info:
            seq = self._alloc_seq(msg_id)
            self._client.send_media_message(target, file_info, msg_id=msg_id, msg_seq=seq)

    @staticmethod
    def _route(target: QQTarget, sender_id: Optional[str] = None) -> ConversationRoute:
        scope_map = {"c2c": "dm", "dm": "dm", "group": "group", "guild": "guild"}
        return ConversationRoute(
            channel="qq",
            scope_type=scope_map.get(target.kind, target.kind),
            scope_id=target.id,
            sender_id=sender_id,
        )

    def handle_message(
        self,
        *,
        kind: str,
        sender_id: str,
        content: str,
        msg_id: str,
        group_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        image_uris: Optional[List[str]] = None,
    ) -> None:
        """Synchronous entry point called by the WebSocket gateway thread.

        Deduplicates, builds the right QQTarget, then submits async dispatch.
        """
        if msg_id:
            if self._deduper.seen_or_record(f"message:{msg_id}"):
                logger.info(
                    "QQ duplicate message ignored: msg_id=%s kind=%s sender=%s",
                    msg_id, kind, sender_id,
                )
                return

        if kind == "group" and group_id:
            target = QQTarget(kind="group", id=group_id)
        elif kind == "guild" and channel_id:
            target = QQTarget(kind="guild", id=channel_id)
        elif kind == "dm" and channel_id:
            target = QQTarget(kind="dm", id=channel_id)
        else:
            target = QQTarget(kind="c2c", id=sender_id)

        self.submit(self._dispatch(target, sender_id, content, msg_id, image_uris=image_uris))

    async def _dispatch(
        self,
        target: QQTarget,
        sender_id: str,
        raw_text: str,
        msg_id: str,
        image_uris: Optional[List[str]] = None,
    ) -> None:
        text = re.sub(r"^<@!?\w+>\s*", "", (raw_text or "").strip()).strip()
        if not text and not image_uris:
            return

        route = self._route(target, sender_id)
        route_key = route.route_key()

        if text.startswith("/"):
            result = await self._bridge.handle_control_command(route, text)
            if result.get("handled"):
                if result.get("clear_pending"):
                    self._clear_pending(route_key)
                reply = result.get("message") or ""
                for chunk in text_chunks(reply, limit=_MAX_TEXT):
                    await asyncio.to_thread(self._send_text, target, chunk, msg_id)
                return

        if self._get_running(route_key) is not None:
            self._queue_message(route_key, text or "[image]")
            await asyncio.to_thread(self._send_text, target, "Queued after current analysis.", msg_id)
            return

        task = asyncio.create_task(
            self._analysis_wrapper(route, target, msg_id, text, image_uris=image_uris)
        )
        self._set_task(route_key, task, text or "[image]")

    async def _analysis_wrapper(
        self,
        route: ConversationRoute,
        target: QQTarget,
        msg_id: Optional[str],
        user_text: str,
        image_uris: Optional[List[str]] = None,
    ) -> None:
        route_key = route.route_key()
        llm_buf: List[str] = []
        image_buf: List[str] = []
        file_buf: List[str] = []
        await asyncio.to_thread(self._send_text, target, "Thinking...", msg_id)

        on_chunk = self.make_chunk_callback(llm_buf)
        on_step = self.make_image_step_callback(llm_buf, image_buf, file_buf=file_buf)

        try:
            result = await self._bridge.run_chat(
                route,
                user_text,
                image_uris=image_uris,
                process_chunk=on_chunk,
                process_step_message=on_step,
            )
            reply = md_to_plain(extract_display_text(result, llm_buf))
            for chunk in text_chunks(reply, limit=_MAX_TEXT):
                await asyncio.to_thread(self._send_text, target, chunk, msg_id)
            for uri in image_buf:
                await asyncio.to_thread(self._send_image, target, uri, msg_id)
            # QQ doesn't support file upload via bot API — mention file names
            if file_buf:
                import os
                names = [os.path.basename(p) for p in file_buf if os.path.isfile(p)]
                if names:
                    await asyncio.to_thread(self._send_text, target, f"📎 Files: {', '.join(names)}", msg_id)
        except asyncio.CancelledError:
            await asyncio.to_thread(self._send_text, target, "Cancelled.", msg_id)
            raise
        except Exception as exc:
            logger.exception("QQ analysis failed")
            await asyncio.to_thread(self._send_text, target, f"Error: {exc}", msg_id)
        finally:
            self._pop_task(route_key)
            queued = self._pop_queued(route_key)
            if queued:
                next_text = "\n\n".join(queued)
                task = asyncio.create_task(
                    self._analysis_wrapper(route, target, None, next_text)
                )
                self._set_task(route_key, task, next_text)


# ── Image extraction from QQ attachments ─────────────────────────────────────

def _extract_attachment_urls(dispatch_data: Dict[str, Any]) -> List[str]:
    """Extract image URLs from QQ dispatch data attachments.

    QQ messages include an ``attachments`` list with image info. Each
    attachment has a ``url`` field pointing to the QQ CDN image.  We
    download them inline (they are short-lived) and convert to data-URIs.
    """
    attachments = dispatch_data.get("attachments") or []
    urls: List[str] = []
    for att in attachments:
        ct = str(att.get("content_type") or "").lower()
        if not ct.startswith("image/"):
            continue
        url = str(att.get("url") or "").strip()
        if url:
            if url.startswith("//"):
                url = f"https:{url}"
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                urls.append(bytes_to_data_uri(resp.content, att.get("filename") or "image.png"))
            except Exception:
                logger.debug("QQ attachment download failed: %s", url)
    return urls


# ── WebSocket Gateway ─────────────────────────────────────────────────────────

def _run_gateway(*, client: QQClient, runtime: QQRuntime, stop_event: threading.Event) -> None:
    """Run the QQ Bot WebSocket gateway in a blocking reconnect loop."""
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError(
            "QQ Bot channel requires 'websocket-client'. Install pantheon-agents[claw]."
        ) from exc

    session_id: Optional[str] = None
    last_seq: Optional[int] = None
    reconnect_attempt = 0

    while not stop_event.is_set():
        try:
            access_token = client._get_access_token()
            gateway_url = client.get_gateway_url()
            logger.info("QQ Bot connecting to %s", gateway_url)

            heartbeat_timer: Optional[threading.Timer] = None
            _ws_ref: list = []

            def _send_heartbeat() -> None:
                ws = _ws_ref[0] if _ws_ref else None
                if ws:
                    try:
                        ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": last_seq}))
                        logger.debug("QQ heartbeat sent (seq=%s)", last_seq)
                    except Exception:
                        pass

            def on_open(ws: Any) -> None:
                _ws_ref.clear()
                _ws_ref.append(ws)
                logger.info("QQ Bot WebSocket connected")

            def on_message(ws: Any, raw: str) -> None:
                nonlocal session_id, last_seq, reconnect_attempt, heartbeat_timer
                logger.info("QQ raw message: %r", raw[:500] if isinstance(raw, (str, bytes)) else repr(raw)[:500])
                try:
                    payload = json.loads(raw)
                except Exception as exc:
                    logger.warning("QQ JSON parse error: %s (raw=%r)", exc, raw[:200] if isinstance(raw, str) else raw[:200])
                    return

                op = payload.get("op")
                s = payload.get("s")
                t = payload.get("t")
                d = payload.get("d") or {}
                logger.info("QQ recv op=%s t=%s", op, t)

                if s is not None:
                    last_seq = s

                if op == _OP_HELLO:
                    interval_ms = (d.get("heartbeat_interval") or 41250) / 1000.0
                    logger.info("QQ Hello, heartbeat_interval=%.1fs", interval_ms)

                    def _hb_loop() -> None:
                        nonlocal heartbeat_timer
                        _send_heartbeat()
                        heartbeat_timer = threading.Timer(interval_ms, _hb_loop)
                        heartbeat_timer.daemon = True
                        heartbeat_timer.start()

                    if heartbeat_timer:
                        heartbeat_timer.cancel()
                    heartbeat_timer = threading.Timer(interval_ms, _hb_loop)
                    heartbeat_timer.daemon = True
                    heartbeat_timer.start()

                    if session_id and last_seq is not None:
                        logger.info("QQ Resuming session %s", session_id)
                        ws.send(json.dumps({
                            "op": _OP_RESUME,
                            "d": {
                                "token": f"QQBot {access_token}",
                                "session_id": session_id,
                                "seq": last_seq,
                            },
                        }))
                    else:
                        logger.info("QQ Identify with intents=%d", _INTENT_FULL)
                        ws.send(json.dumps({
                            "op": _OP_IDENTIFY,
                            "d": {
                                "token": f"QQBot {access_token}",
                                "intents": _INTENT_FULL,
                                "shard": [0, 1],
                            },
                        }))
                    return

                if op == _OP_DISPATCH:
                    reconnect_attempt = 0  # successful dispatch resets backoff
                    if t == "READY":
                        session_id = d.get("session_id")
                        logger.info("QQ Ready, session_id=%s", session_id)

                    elif t == "C2C_MESSAGE_CREATE":
                        openid = (d.get("author") or {}).get("user_openid", "")
                        content = d.get("content", "")
                        msg_id = d.get("id", "")
                        img_uris = _extract_attachment_urls(d)
                        if openid and (content.strip() or img_uris):
                            runtime.handle_message(
                                kind="c2c",
                                sender_id=openid,
                                content=content,
                                msg_id=msg_id,
                                image_uris=img_uris or None,
                            )

                    elif t == "GROUP_AT_MESSAGE_CREATE":
                        author = d.get("author") or {}
                        member_openid = author.get("member_openid", "")
                        group_openid = d.get("group_openid", "")
                        content = d.get("content", "")
                        msg_id = d.get("id", "")
                        img_uris = _extract_attachment_urls(d)
                        if content.strip() or img_uris:
                            runtime.handle_message(
                                kind="group",
                                sender_id=member_openid,
                                content=content,
                                msg_id=msg_id,
                                group_id=group_openid,
                                image_uris=img_uris or None,
                            )

                    elif t == "AT_MESSAGE_CREATE":
                        author = d.get("author") or {}
                        sender_id = author.get("id", "")
                        channel_id = d.get("channel_id", "")
                        content = d.get("content", "")
                        msg_id = d.get("id", "")
                        img_uris = _extract_attachment_urls(d)
                        if content.strip() or img_uris:
                            runtime.handle_message(
                                kind="guild",
                                sender_id=sender_id,
                                content=content,
                                msg_id=msg_id,
                                channel_id=channel_id,
                                image_uris=img_uris or None,
                            )

                    elif t == "DIRECT_MESSAGE_CREATE":
                        author = d.get("author") or {}
                        sender_id = author.get("id", "")
                        guild_id = d.get("guild_id", "")
                        content = d.get("content", "")
                        msg_id = d.get("id", "")
                        img_uris = _extract_attachment_urls(d)
                        if content.strip() or img_uris:
                            runtime.handle_message(
                                kind="dm",
                                sender_id=sender_id,
                                content=content,
                                msg_id=msg_id,
                                channel_id=guild_id,
                                image_uris=img_uris or None,
                            )
                    return

                if op == _OP_RECONNECT:
                    logger.info("QQ Server requested reconnect")
                    ws.close()
                elif op == _OP_INVALID_SESSION:
                    can_resume = bool(d)
                    logger.warning("QQ Invalid session, can_resume=%s", can_resume)
                    if not can_resume:
                        session_id = None
                        last_seq = None
                    ws.close()
                elif op == _OP_HEARTBEAT_ACK:
                    logger.debug("QQ Heartbeat ACK")

            def on_error(ws: Any, error: Any) -> None:
                logger.warning("QQ WebSocket error: %s", error, exc_info=isinstance(error, Exception))

            def on_close(ws: Any, code: Any, reason: Any) -> None:
                nonlocal heartbeat_timer
                logger.info("QQ WebSocket closed (code=%s reason=%s)", code, reason)
                if heartbeat_timer:
                    heartbeat_timer.cancel()
                    heartbeat_timer = None

            ws_app = websocket.WebSocketApp(
                gateway_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            # Stop-event watcher: close the WebSocket when stop is requested
            def _watch_stop() -> None:
                stop_event.wait()
                try:
                    ws_app.close()
                except Exception:
                    pass

            watcher = threading.Thread(target=_watch_stop, daemon=True)
            watcher.start()
            ws_app.run_forever()

        except Exception as exc:
            logger.error("QQ gateway error: %s", exc)

        if stop_event.is_set():
            break

        delay = _RECONNECT_DELAYS[min(reconnect_attempt, len(_RECONNECT_DELAYS) - 1)]
        reconnect_attempt += 1
        logger.info("QQ reconnecting in %ss (attempt %s)...", delay, reconnect_attempt)
        stop_event.wait(timeout=delay)


# ── Public entry point ────────────────────────────────────────────────────────

async def run_qq_channel(
    *,
    bridge: Any,
    config: Dict[str, Any],
    stop_event: threading.Event,
) -> None:
    client = QQClient(
        str(config.get("app_id") or ""),
        str(config.get("client_secret") or ""),
    )
    runtime = QQRuntime(bridge=bridge, client=client)
    try:
        await asyncio.to_thread(_run_gateway, client=client, runtime=runtime, stop_event=stop_event)
    finally:
        runtime.stop()
