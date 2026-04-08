from __future__ import annotations

import asyncio
import base64
import io
import threading
import time
from typing import Any

from slack_bolt.app.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

import aiohttp
from PIL import Image

from pantheon.claw.registry import ConversationRoute
from pantheon.claw.runtime import ChannelRuntime, data_uri_to_bytes, bytes_to_data_uri, text_chunks, md_to_slack, extract_display_text

from pantheon.utils.log import logger

_EDIT_GAP_SECONDS = 1.5


class SlackGatewayApp(ChannelRuntime):
    def __init__(self, *, bridge: Any, config: dict[str, Any], stop_event: threading.Event) -> None:
        super().__init__(bridge=bridge)
        self._app_token = str(config.get("app_token") or "")
        self._bot_token = str(config.get("bot_token") or "")
        self._stop_event = stop_event
        self._app = AsyncApp(token=self._bot_token)
        self._bind_routes()

    def _route_from_event(self, body: dict[str, Any]) -> ConversationRoute:
        event = body["event"]
        channel_id = str(event.get("channel") or "")
        thread_ts = event.get("thread_ts")
        if channel_id.startswith("D"):
            return ConversationRoute(
                channel="slack",
                scope_type="dm",
                scope_id=channel_id,
                sender_id=str(event.get("user") or ""),
            )
        return ConversationRoute(
            channel="slack",
            scope_type="channel",
            scope_id=channel_id,
            thread_id=str(thread_ts or event.get("ts") or ""),
            sender_id=str(event.get("user") or ""),
        )

    def _command_parts(self, text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "", text
        pieces = text.split(maxsplit=1)
        return pieces[0].lower(), pieces[1].strip() if len(pieces) > 1 else ""

    async def _post(self, client, body: dict[str, Any], text: str, *, thread: bool = False) -> dict[str, Any]:
        event = body["event"]
        params: dict[str, Any] = {
            "channel": event["channel"],
            "text": text,
        }
        if thread:
            params["thread_ts"] = event.get("thread_ts") or event.get("ts")
        return await client.chat_postMessage(**params)

    async def _update(self, client, body: dict[str, Any], message_ts: str, text: str) -> None:
        await client.chat_update(
            channel=body["event"]["channel"],
            ts=message_ts,
            text=text,
        )

    async def _handle_control(self, route: ConversationRoute, body: dict[str, Any], client, text: str) -> bool:
        result = await self._bridge.handle_control_command(route, text)
        if not result.get("handled"):
            return False
        if result.get("clear_pending"):
            self._clear_pending(route.route_key())
        await self._post(client, body, result.get("message") or "", thread=bool(route.thread_id))
        return True

    async def _download_files(self, client, event: dict[str, Any]) -> list[str]:
        """Download image files from a Slack event and return data-URI list.

        Images are normalised through PIL so the data-URI always contains
        a format that OpenAI / other LLM providers accept (PNG or JPEG).
        """
        files = event.get("files") or []
        uris: list[str] = []
        for f in files:
            mimetype = f.get("mimetype") or ""
            if not mimetype.startswith("image/"):
                continue
            url = f.get("url_private_download") or f.get("url_private") or ""
            if not url:
                continue
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers={"Authorization": f"Bearer {self._bot_token}"},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning("Slack file download HTTP %s for %s", resp.status, f.get("name"))
                            continue
                        data = await resp.read()
                # Normalise through PIL → always PNG or JPEG
                img = Image.open(io.BytesIO(data))
                buf = io.BytesIO()
                if img.mode in ("RGBA", "LA", "P"):
                    img.save(buf, format="PNG")
                    out_mime = "png"
                else:
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    img.save(buf, format="JPEG", quality=85)
                    out_mime = "jpeg"
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                uris.append(f"data:image/{out_mime};base64,{encoded}")
            except Exception:
                logger.exception("Slack file download/convert failed: %s", f.get("name"))
        return uris

    async def _send_image(self, client, channel: str, thread_ts: str | None, data_uri: str) -> None:
        """Upload a base64 data-URI as a file to Slack."""
        raw, mime = data_uri_to_bytes(data_uri)
        if not raw:
            return
        ext = mime.split("/")[-1] if mime else "png"
        try:
            await client.files_upload_v2(
                channel=channel,
                content=raw,
                filename=f"image.{ext}",
                thread_ts=thread_ts,
            )
        except Exception:
            logger.exception("Slack image upload failed")

    async def _analysis_wrapper(
        self,
        route: ConversationRoute,
        body: dict[str, Any],
        client,
        user_text: str,
        image_uris: list[str] | None = None,
    ) -> None:
        route_key = route.route_key()
        placeholder = await self._post(client, body, ":thinking_face: Thinking...", thread=bool(route.thread_id))
        placeholder_ts = str(placeholder["ts"])
        llm_buf: list[str] = []
        image_buf: list[str] = []
        file_buf: list[str] = []
        last_progress = ""
        last_edit = 0.0

        async def _refresh(force: bool = False) -> None:
            nonlocal last_edit
            now = time.monotonic()
            if not force and (now - last_edit) < _EDIT_GAP_SECONDS:
                return
            last_edit = now
            llm_text = "".join(llm_buf).strip()
            if llm_text:
                preview = llm_text
            elif last_progress:
                preview = f"🤖 Agent is working...\n\n{last_progress}"
            else:
                preview = "🤖 Thinking..."
            await self._update(client, body, placeholder_ts, md_to_slack(preview[-2800:]))

        async def _set_progress(label: str) -> None:
            nonlocal last_progress
            last_progress = label

        on_chunk = self.make_chunk_callback(llm_buf, on_update=lambda: _refresh(False))
        on_step = self.make_image_step_callback(
            llm_buf,
            image_buf,
            file_buf=file_buf,
            progress_cb=_set_progress,
            refresh_cb=lambda: _refresh(True),
        )

        try:
            result = await self._bridge.run_chat(
                route,
                user_text,
                image_uris=image_uris,
                process_chunk=on_chunk,
                process_step_message=on_step,
            )
            final_text = extract_display_text(result, llm_buf)
            await self._update(client, body, placeholder_ts, md_to_slack(final_text[-3500:]))
            # Send any response images and files
            event = body["event"]
            channel = event["channel"]
            thread_ts = event.get("thread_ts") or event.get("ts")
            for uri in image_buf:
                await self._send_image(client, channel, thread_ts, uri)
            for fpath in file_buf:
                import os
                if os.path.isfile(fpath):
                    try:
                        await client.files_upload_v2(
                            channel=channel,
                            thread_ts=thread_ts,
                            file=fpath,
                            filename=os.path.basename(fpath),
                        )
                    except Exception:
                        logger.warning("Slack file send failed: %s", fpath)
        except asyncio.CancelledError:
            await self._update(client, body, placeholder_ts, "Cancelled.")
            raise
        except Exception as exc:
            logger.exception("Slack analysis failed")
            await self._update(client, body, placeholder_ts, f"Error: {exc}")
        finally:
            self._pop_task(route_key)
            queued = self._pop_queued(route_key)
            if queued:
                next_text = "\n\n".join(queued)
                task = asyncio.create_task(self._analysis_wrapper(route, body, client, next_text))
                self._set_task(route_key, task, next_text)

    def _bind_routes(self) -> None:
        @self._app.event("message")
        async def _handle_message(body, client, ack):
            await ack()
            event = body["event"]
            if event.get("subtype") == "bot_message":
                return
            route = self._route_from_event(body)
            if route.scope_type != "dm" and not event.get("thread_ts"):
                return
            text = str(event.get("text") or "").strip()
            image_uris = await self._download_files(client, event)
            if not text and not image_uris:
                return
            cmd, tail = self._command_parts(text)
            if cmd and await self._handle_control(route, body, client, text):
                return
            route_key = route.route_key()
            if self._get_running(route_key) is not None:
                self._queue_message(route_key, tail or text or "[image]")
                await self._post(client, body, "Queued after current analysis.", thread=bool(route.thread_id))
                return
            task = asyncio.create_task(
                self._analysis_wrapper(route, body, client, tail or text, image_uris=image_uris or None)
            )
            self._set_task(route_key, task, tail or text or "[image]")

        @self._app.event("app_mention")
        async def _handle_mention(body, client, ack):
            await ack()
            route = self._route_from_event(body)
            event = body["event"]
            text = str(event.get("text") or "").strip()
            parts = text.split(maxsplit=1)
            cleaned = parts[1] if len(parts) > 1 else text
            image_uris = await self._download_files(client, event)
            cmd, tail = self._command_parts(cleaned)
            if cmd and await self._handle_control(route, body, client, cleaned):
                return
            route_key = route.route_key()
            if self._get_running(route_key) is not None:
                self._queue_message(route_key, tail or cleaned or "[image]")
                await self._post(client, body, "Queued after current analysis.", thread=True)
                return
            task = asyncio.create_task(
                self._analysis_wrapper(route, body, client, tail or cleaned, image_uris=image_uris or None)
            )
            self._set_task(route_key, task, tail or cleaned or "[image]")

    async def run(self) -> None:
        handler = AsyncSocketModeHandler(self._app, self._app_token)
        logger.info("Slack Socket Mode handler starting")
        watcher = asyncio.create_task(asyncio.to_thread(self._stop_event.wait))
        runner = asyncio.create_task(handler.start_async())
        try:
            done, pending = await asyncio.wait({watcher, runner}, return_when=asyncio.FIRST_COMPLETED)
            if runner in done and not runner.cancelled():
                exc = runner.exception()
                if exc:
                    logger.exception("Slack Socket Mode handler failed", exc_info=exc)
                    raise exc
        finally:
            for task in (watcher, runner):
                if not task.done():
                    task.cancel()
            logger.info("Slack Socket Mode handler stopped")


async def run_slack_channel(*, bridge: Any, config: dict[str, Any], stop_event: threading.Event) -> None:
    app = SlackGatewayApp(bridge=bridge, config=config, stop_event=stop_event)
    await app.run()
