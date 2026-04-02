from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import threading
import time
import uuid
from typing import Any

import requests

from pantheon.claw.registry import ConversationRoute
from pantheon.claw.runtime import ChannelRuntime, text_chunks

logger = logging.getLogger("pantheon.claw.channels.wechat")

_DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
_DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
_MAX_TEXT = 3600


def _extract_text(item_list: Any) -> str:
    if not isinstance(item_list, list):
        return ""
    parts: list[str] = []
    for item in item_list:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == 1:
            text = str(((item.get("text_item") or {}).get("text") or "")).strip()
            if text:
                parts.append(text)
        elif item_type == 3:
            text = str(((item.get("voice_item") or {}).get("text") or "")).strip()
            if text:
                parts.append(text)
        elif item_type == 2:
            parts.append("[image]")
        elif item_type == 4:
            parts.append("[file]")
        elif item_type == 5:
            parts.append("[video]")
    return "\n".join(parts).strip()


class WeChatApiClient:
    def __init__(self, *, token: str, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._token = str(token or "").strip()
        self._base_url = str(base_url or _DEFAULT_BASE_URL).rstrip("/")

    @staticmethod
    def _random_wechat_uin() -> str:
        return base64.b64encode(str(secrets.randbits(32)).encode("utf-8")).decode("ascii")

    def _headers(self, body: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self._token}",
            "X-WECHAT-UIN": self._random_wechat_uin(),
            "Content-Length": str(len(body.encode("utf-8"))),
        }

    def _get_headers(self) -> dict[str, str]:
        return {
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self._token}",
            "X-WECHAT-UIN": self._random_wechat_uin(),
        }

    def _get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: tuple[float, float] = (10.0, 20.0),
    ) -> dict[str, Any]:
        response = requests.get(
            f"{self._base_url}{endpoint}",
            params=params,
            headers=self._get_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"WeChat API returned non-object response for {endpoint}")
        return data

    def get_qrcode(self) -> dict[str, Any]:
        return self._get_json("/ilink/bot/get_bot_qrcode", params={"bot_type": "3"})

    def get_qrcode_status(self, qrcode_id: str) -> dict[str, Any]:
        return self._get_json("/ilink/bot/get_qrcode_status", params={"qrcode": qrcode_id})

    def _post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        timeout: tuple[float, float],
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False)
        response = requests.post(
            f"{self._base_url}{endpoint}",
            data=body.encode("utf-8"),
            headers=self._headers(body),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"WeChat API returned non-object response for {endpoint}")
        return data

    def get_updates(self, *, cursor: str, timeout_ms: int = _DEFAULT_LONG_POLL_TIMEOUT_MS) -> dict[str, Any]:
        payload = {
            "get_updates_buf": cursor or "",
            "base_info": {"channel_version": "pantheonclaw"},
        }
        read_timeout = max(5.0, (float(timeout_ms) / 1000.0) + 5.0)
        return self._post_json("/ilink/bot/getupdates", payload, timeout=(10.0, read_timeout))

    def send_text(self, *, to_user_id: str, text: str, context_token: str) -> None:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": uuid.uuid4().hex,
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": {"channel_version": "pantheonclaw"},
        }
        data = self._post_json("/ilink/bot/sendmessage", payload, timeout=(10.0, 20.0))
        errcode = data.get("errcode")
        ret = data.get("ret")
        if (isinstance(errcode, int) and errcode != 0) or (isinstance(ret, int) and ret != 0):
            raise RuntimeError(data.get("errmsg") or f"WeChat send failed (ret={ret}, errcode={errcode})")


class WeChatGatewayBot(ChannelRuntime):
    def __init__(self, *, bridge: Any, config: dict[str, Any], stop_event: threading.Event) -> None:
        super().__init__(bridge=bridge)
        self._stop_event = stop_event
        self._allow_from = {str(item).strip() for item in (config.get("allow_from") or []) if str(item).strip()}
        self._client = WeChatApiClient(
            token=str(config.get("token") or ""),
            base_url=str(config.get("base_url") or _DEFAULT_BASE_URL),
        )
        self._contexts: dict[str, tuple[str, str]] = {}

    def _command_parts(self, text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "", text
        parts = text.split(maxsplit=1)
        return parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""

    async def _send_text(self, to_user_id: str, context_token: str, text: str) -> None:
        for chunk in text_chunks(text, limit=_MAX_TEXT):
            await asyncio.to_thread(
                self._client.send_text,
                to_user_id=to_user_id,
                text=chunk,
                context_token=context_token,
            )

    async def _handle_control(
        self,
        route: ConversationRoute,
        *,
        to_user_id: str,
        context_token: str,
        text: str,
    ) -> bool:
        result = await self._bridge.handle_control_command(route, text)
        if not result.get("handled"):
            return False
        if result.get("clear_pending"):
            self._clear_pending(route.route_key())
        await self._send_text(to_user_id, context_token, result.get("message") or "")
        return True

    async def _analysis_wrapper(
        self,
        route: ConversationRoute,
        *,
        to_user_id: str,
        context_token: str,
        user_text: str,
    ) -> None:
        route_key = route.route_key()
        llm_buf: list[str] = []

        # WeChat has no message-edit API — use callbacks only for correct buffer assembly
        on_chunk = self.make_chunk_callback(llm_buf)
        on_step = self.make_step_callback(llm_buf)

        try:
            result = await self._bridge.run_chat(
                route,
                user_text,
                process_chunk=on_chunk,
                process_step_message=on_step,
            )
            final = str(result.get("response") or "".join(llm_buf) or "Done.")
            await self._send_text(to_user_id, context_token, final)
        except asyncio.CancelledError:
            await self._send_text(to_user_id, context_token, "Cancelled.")
            raise
        except Exception as exc:
            logger.exception("WeChat analysis failed")
            await self._send_text(to_user_id, context_token, f"Error: {exc}")
        finally:
            self._pop_task(route_key)
            queued = self._pop_queued(route_key)
            latest = self._contexts.get(route_key, (to_user_id, context_token))
            if queued:
                next_text = "\n\n".join(queued)
                task = asyncio.create_task(
                    self._analysis_wrapper(
                        route,
                        to_user_id=latest[0],
                        context_token=latest[1],
                        user_text=next_text,
                    )
                )
                self._set_task(route_key, task, next_text)

    async def _on_message(self, raw: dict[str, Any]) -> None:
        from_user_id = str(raw.get("from_user_id") or "").strip()
        context_token = str(raw.get("context_token") or "").strip()
        group_id = str(raw.get("group_id") or "").strip()
        if not from_user_id or not context_token:
            return
        if from_user_id.endswith("@im.bot"):
            return
        if self._allow_from and from_user_id not in self._allow_from:
            return
        if group_id:
            logger.info("Ignoring WeChat group message group_id=%s from=%s", group_id, from_user_id)
            return
        text = _extract_text(raw.get("item_list"))
        if not text:
            return

        route = ConversationRoute(
            channel="wechat",
            scope_type="dm",
            scope_id=from_user_id,
            sender_id=from_user_id,
        )
        route_key = route.route_key()
        self._contexts[route_key] = (from_user_id, context_token)
        cmd, tail = self._command_parts(text)
        if cmd and await self._handle_control(route, to_user_id=from_user_id, context_token=context_token, text=text):
            return

        if self._get_running(route_key) is not None:
            self._queue_message(route_key, tail or text)
            await self._send_text(from_user_id, context_token, "Queued after current analysis.")
            return

        await self._send_text(from_user_id, context_token, "Thinking...")
        task = asyncio.create_task(
            self._analysis_wrapper(
                route,
                to_user_id=from_user_id,
                context_token=context_token,
                user_text=tail or text,
            )
        )
        self._set_task(route_key, task, tail or text)

    async def run(self) -> None:
        logger.info("WeChat long-poll starting (base_url=%s)", self._client._base_url)
        cursor = ""
        timeout_ms = _DEFAULT_LONG_POLL_TIMEOUT_MS
        while not self._stop_event.is_set():
            try:
                data = await asyncio.to_thread(
                    self._client.get_updates,
                    cursor=cursor,
                    timeout_ms=timeout_ms,
                )
                next_cursor = str(data.get("get_updates_buf") or "")
                if next_cursor:
                    cursor = next_cursor
                next_timeout = data.get("longpolling_timeout_ms")
                if isinstance(next_timeout, int) and next_timeout > 0:
                    timeout_ms = next_timeout
                errcode = data.get("errcode")
                ret = data.get("ret")
                if (isinstance(errcode, int) and errcode != 0) or (isinstance(ret, int) and ret != 0):
                    raise RuntimeError(data.get("errmsg") or f"WeChat getupdates failed (ret={ret}, errcode={errcode})")
                for raw in data.get("msgs") or []:
                    if isinstance(raw, dict):
                        await self._on_message(raw)
            except requests.exceptions.ReadTimeout:
                continue
            except Exception as exc:
                logger.warning("WeChat polling error: %s", exc)
                if self._stop_event.wait(2.0):
                    break


async def run_wechat_channel(*, bridge: Any, config: dict[str, Any], stop_event: threading.Event) -> None:
    bot = WeChatGatewayBot(bridge=bridge, config=config, stop_event=stop_event)
    await bot.run()
