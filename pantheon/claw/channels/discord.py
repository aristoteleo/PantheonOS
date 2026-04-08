from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import discord

import aiohttp

from pantheon.claw.registry import ConversationRoute
from pantheon.claw.runtime import ChannelRuntime, data_uri_to_bytes, bytes_to_data_uri, text_chunks, extract_display_text

from pantheon.utils.log import logger

_EDIT_GAP = 1.5
_MAX_MSG = 1900  # Discord message limit (leaving headroom)


class DiscordGatewayBot(discord.Client, ChannelRuntime):
    def __init__(
        self,
        *,
        bridge: Any,
        config: dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.message_content = True
        discord.Client.__init__(self, intents=intents)
        ChannelRuntime.__init__(self, bridge=bridge)
        self._token = str(config.get("token") or "")
        self._stop_event = stop_event

    # ── Lifecycle events ──────────────────────────────────────────────────────

    async def on_connect(self) -> None:
        logger.info("Discord WebSocket connected")

    async def on_ready(self) -> None:
        logger.info("Discord bot ready as %s (%s)", self.user, getattr(self.user, "id", "?"))

    async def on_disconnect(self) -> None:
        logger.warning("Discord gateway disconnected")

    async def on_resumed(self) -> None:
        logger.info("Discord gateway session resumed")

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        logger.exception("Discord client error in %s", event_method)

    # ── Routing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _route_from_message(message: discord.Message) -> ConversationRoute:
        scope_type = "dm" if isinstance(message.channel, discord.DMChannel) else "guild"
        thread_id = (
            str(message.channel.id)
            if isinstance(message.channel, discord.Thread)
            else None
        )
        return ConversationRoute(
            channel="discord",
            scope_type=scope_type,
            scope_id=str(message.guild.id if message.guild else message.channel.id),
            thread_id=thread_id,
            sender_id=str(message.author.id),
        )

    def _should_handle(self, message: discord.Message) -> bool:
        if isinstance(message.channel, discord.DMChannel):
            return True
        if self.user is None:
            return False
        return self.user in message.mentions

    # ── Image helpers ─────────────────────────────────────────────────────────

    @staticmethod
    async def _download_attachments(message: discord.Message) -> list[str]:
        """Download image attachments and return data-URI list."""
        uris: list[str] = []
        for att in message.attachments:
            ct = att.content_type or ""
            if not ct.startswith("image/"):
                continue
            try:
                data = await att.read()
                uris.append(bytes_to_data_uri(data, att.filename or "image.png"))
            except Exception:
                logger.debug("Discord attachment download failed: %s", att.filename)
        return uris

    @staticmethod
    async def _send_image(channel, data_uri: str) -> None:
        """Send a base64 data-URI as a file to a Discord channel."""
        import io as _io
        raw, mime = data_uri_to_bytes(data_uri)
        if not raw:
            return
        ext = mime.split("/")[-1] if mime else "png"
        buf = _io.BytesIO(raw)
        try:
            await channel.send(file=discord.File(buf, filename=f"image.{ext}"))
        except Exception:
            logger.warning("Discord image send failed")

    # ── Analysis wrapper ─────────────────────────────────────────────────────

    async def _analysis_wrapper(
        self,
        route: ConversationRoute,
        message: discord.Message,
        user_text: str,
        image_uris: list[str] | None = None,
    ) -> None:
        route_key = route.route_key()
        placeholder = await message.reply("Thinking...")
        llm_buf: list[str] = []
        image_buf: list[str] = []
        file_buf: list[str] = []
        last_progress = ""
        last_edit = 0.0

        async def _refresh(force: bool = False) -> None:
            nonlocal last_edit
            now = time.monotonic()
            if not force and (now - last_edit) < _EDIT_GAP:
                return
            last_edit = now
            llm_text = "".join(llm_buf).strip()
            if llm_text:
                preview = llm_text
            elif last_progress:
                preview = f"🤖 Agent is working...\n\n{last_progress}"
            else:
                preview = "🤖 Thinking..."
            try:
                await placeholder.edit(content=preview[-_MAX_MSG:])
            except Exception:
                pass

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
            import re as _re
            final = extract_display_text(result, llm_buf)
            # Strip image links (images are sent separately); keep other markdown
            final = _re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", "", final)
            chunks = text_chunks(final, limit=_MAX_MSG)
            try:
                await placeholder.edit(content=chunks[0] if chunks else "Done.")
            except Exception:
                pass
            for extra in chunks[1:]:
                try:
                    await message.reply(extra)
                except Exception:
                    pass
            for uri in image_buf:
                await self._send_image(message.channel, uri)
            for fpath in file_buf:
                import os
                if os.path.isfile(fpath):
                    try:
                        await message.channel.send(file=discord.File(fpath))
                    except Exception:
                        logger.warning("Discord file send failed: %s", fpath)
        except asyncio.CancelledError:
            try:
                await placeholder.edit(content="Cancelled.")
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.exception("Discord analysis failed")
            try:
                await placeholder.edit(content=f"Error: {exc}")
            except Exception:
                pass
        finally:
            self._pop_task(route_key)
            queued = self._pop_queued(route_key)
            if queued:
                next_text = "\n\n".join(queued)
                task = asyncio.create_task(self._analysis_wrapper(route, message, next_text))
                self._set_task(route_key, task, next_text)

    # ── Discord event handler ─────────────────────────────────────────────────

    async def on_message(self, message: discord.Message) -> None:
        try:
            await self._handle_message(message)
        except Exception:
            logger.exception("Discord on_message handler failed")

    async def _handle_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self._should_handle(message):
            return

        route = self._route_from_message(message)
        route_key = route.route_key()

        # Download image attachments
        image_uris = await self._download_attachments(message)

        text = message.content or ""
        if self.user is not None and not isinstance(message.channel, discord.DMChannel):
            text = text.replace(self.user.mention, "").strip()

        if not text and not image_uris:
            return

        if text.startswith("/"):
            result = await self._bridge.handle_control_command(route, text)
            if result.get("handled"):
                if result.get("clear_pending"):
                    self._clear_pending(route_key)
                reply = result.get("message") or ""
                for chunk in text_chunks(reply, limit=_MAX_MSG):
                    await message.reply(chunk)
                return

        parts = text.split(maxsplit=1) if text.startswith("/") else ["", text]
        body = parts[1].strip() if len(parts) > 1 else text

        running = self._get_running(route_key)
        if running is not None:
            self._queue_message(route_key, body or "[image]")
            await message.reply("Queued after current analysis.")
            return

        task = asyncio.create_task(
            self._analysis_wrapper(route, message, body, image_uris=image_uris or None)
        )
        self._set_task(route_key, task, body or "[image]")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def run_gateway(self) -> None:
        watcher = asyncio.create_task(asyncio.to_thread(self._stop_event.wait))
        try:
            logger.info("Discord client starting")
            await self.start(self._token)
            logger.warning("Discord client.start() returned unexpectedly")
        except Exception:
            logger.exception("Discord client exited with error")
            raise
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            if not self.is_closed():
                await self.close()


async def run_discord_channel(
    *,
    bridge: Any,
    config: dict[str, Any],
    stop_event: threading.Event,
) -> None:
    client = DiscordGatewayBot(bridge=bridge, config=config, stop_event=stop_event)
    await client.run_gateway()
