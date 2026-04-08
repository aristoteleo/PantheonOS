from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from pantheon.claw.registry import ConversationRoute
from pantheon.claw.runtime import ChannelRuntime, data_uri_to_bytes, bytes_to_data_uri, text_chunks, md_to_telegram, md_to_plain, extract_display_text

from pantheon.utils.log import logger

_EDIT_GAP = 1.5   # minimum seconds between placeholder edits
_MAX_MSG = 4096   # Telegram message length limit


class TelegramGatewayBot(ChannelRuntime):
    def __init__(
        self,
        *,
        bridge: Any,
        config: dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(bridge=bridge)
        self._token = str(config.get("token") or "")
        self._allowed_users = {
            str(item) for item in (config.get("allowed_users") or []) if str(item).strip()
        }
        self._stop_event = stop_event
        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, self._handle_photo))
        self._app.add_handler(MessageHandler(filters.Document.ALL & ~filters.Document.IMAGE, self._handle_document))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        self._app.add_handler(MessageHandler(filters.COMMAND, self._handle_text))

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _allowed(self, update: Update) -> bool:
        if not self._allowed_users:
            return True
        user = update.effective_user
        if user is None:
            return False
        return (
            str(user.id) in self._allowed_users
            or (user.username and user.username in self._allowed_users)
        )

    # ── Routing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _route_from_update(update: Update) -> ConversationRoute:
        chat = update.effective_chat
        message = update.effective_message
        scope_type = "dm" if chat and chat.type == ChatType.PRIVATE else "group"
        thread_id = getattr(message, "message_thread_id", None)
        return ConversationRoute(
            channel="telegram",
            scope_type=scope_type,
            scope_id=str(chat.id if chat else ""),
            thread_id=str(thread_id) if thread_id else None,
            sender_id=str(update.effective_user.id if update.effective_user else ""),
        )

    # ── Analysis wrapper ─────────────────────────────────────────────────────

    async def _download_photo(self, update: Update) -> list[str]:
        """Download photo(s) from a Telegram message and return data-URI list."""
        message = update.effective_message
        if message is None:
            return []
        uris: list[str] = []
        if message.photo:
            photo = message.photo[-1]  # highest resolution
            tg_file = await photo.get_file()
            data = await tg_file.download_as_bytearray()
            uris.append(bytes_to_data_uri(bytes(data), "photo.jpg"))
        if message.document and (message.document.mime_type or "").startswith("image/"):
            tg_file = await message.document.get_file()
            data = await tg_file.download_as_bytearray()
            uris.append(bytes_to_data_uri(bytes(data), message.document.file_name or "image.png"))
        return uris

    async def _send_image(self, message, data_uri: str) -> None:
        """Send a base64 data-URI as a photo to the Telegram chat."""
        raw, mime = data_uri_to_bytes(data_uri)
        if not raw:
            return
        import io
        ext = mime.split("/")[-1] if mime else "png"
        buf = io.BytesIO(raw)
        buf.name = f"image.{ext}"
        try:
            await message.reply_photo(photo=buf)
        except Exception:
            logger.debug("Telegram send_photo failed, falling back to document")
            buf.seek(0)
            try:
                await message.reply_document(document=buf)
            except Exception:
                logger.warning("Telegram image send failed completely")

    @staticmethod
    async def _send_file(message, file_path: str) -> None:
        """Send a local file as a Telegram document."""
        import os
        if not os.path.isfile(file_path):
            return
        try:
            with open(file_path, "rb") as f:
                await message.reply_document(document=f, filename=os.path.basename(file_path))
        except Exception:
            logger.warning("Telegram file send failed: %s", file_path)

    async def _analysis_wrapper(
        self,
        route: ConversationRoute,
        update: Update,
        user_text: str,
        image_uris: list[str] | None = None,
    ) -> None:
        route_key = route.route_key()
        message = update.effective_message
        if message is None:
            return

        placeholder = await message.reply_text("Thinking...")
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
            converted = md_to_telegram(preview[-3500:])
            try:
                await placeholder.edit_text(converted, parse_mode="MarkdownV2")
            except Exception:
                try:
                    await placeholder.edit_text(md_to_plain(preview[-3500:]))
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
            final = extract_display_text(result, llm_buf)
            converted_final = md_to_telegram(final[-3500:])
            try:
                await placeholder.edit_text(converted_final, parse_mode="MarkdownV2")
            except Exception:
                try:
                    await placeholder.edit_text(md_to_plain(final[-3500:]))
                except Exception:
                    pass
            for extra in text_chunks(final, limit=_MAX_MSG)[1:]:
                try:
                    await message.reply_text(md_to_telegram(extra), parse_mode="MarkdownV2")
                except Exception:
                    try:
                        await message.reply_text(md_to_plain(extra))
                    except Exception:
                        pass
            # Send any images from the response
            for uri in image_buf:
                await self._send_image(message, uri)
            # Send any files (PDF, markdown, etc.)
            for fpath in file_buf:
                await self._send_file(message, fpath)
        except asyncio.CancelledError:
            try:
                await placeholder.edit_text("Cancelled.")
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.exception("Telegram analysis failed")
            try:
                await placeholder.edit_text(f"Error: {exc}")
            except Exception:
                pass
        finally:
            self._pop_task(route_key)
            queued = self._pop_queued(route_key)
            if queued:
                next_text = "\n\n".join(queued)
                task = asyncio.create_task(self._analysis_wrapper(route, update, next_text))
                self._set_task(route_key, task, next_text)

    # ── Message handler ───────────────────────────────────────────────────────

    async def _handle_photo(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self._allowed(update):
            return
        message = update.effective_message
        if message is None:
            return

        image_uris = await self._download_photo(update)
        if not image_uris:
            return

        text = (message.caption or "").strip()
        route = self._route_from_update(update)
        route_key = route.route_key()

        running = self._get_running(route_key)
        if running is not None:
            self._queue_message(route_key, text or "[image]")
            await message.reply_text("Queued after current analysis.")
            return

        task = asyncio.create_task(
            self._analysis_wrapper(route, update, text, image_uris=image_uris)
        )
        self._set_task(route_key, task, text or "[image]")

    async def _handle_document(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle non-image file uploads (PDF, CSV, etc.)."""
        if not self._allowed(update):
            return
        message = update.effective_message
        if message is None or message.document is None:
            return

        doc = message.document
        file_name = doc.file_name or "uploaded_file"
        caption = (message.caption or "").strip()

        # Download file to a temporary location
        import os
        import tempfile
        tmp_dir = os.path.join(tempfile.gettempdir(), "pantheon_claw_uploads")
        os.makedirs(tmp_dir, exist_ok=True)
        local_path = os.path.join(tmp_dir, file_name)

        try:
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(local_path)
        except Exception:
            logger.warning("Failed to download Telegram document: %s", file_name)
            await message.reply_text(f"Failed to download file: {file_name}")
            return

        # Build message with attachment info (same format as frontend pin-file)
        attachment_text = (
            f"--- Attachments ---\n"
            f"User attached the following files:\n"
            f"{file_name}: {local_path}\n"
            f"--- End of Attachments ---\n"
        )
        user_text = attachment_text + (caption or f"I've uploaded {file_name}. Please process it.")

        route = self._route_from_update(update)
        route_key = route.route_key()

        running = self._get_running(route_key)
        if running is not None:
            self._queue_message(route_key, user_text)
            await message.reply_text("Queued after current analysis.")
            return

        task = asyncio.create_task(self._analysis_wrapper(route, update, user_text))
        self._set_task(route_key, task, f"[file: {file_name}]")

    async def _handle_text(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self._allowed(update):
            return
        message = update.effective_message
        if message is None:
            return
        text = (message.text or "").strip()
        if not text:
            return

        route = self._route_from_update(update)
        route_key = route.route_key()

        if text.startswith("/"):
            result = await self._bridge.handle_control_command(route, text)
            if result.get("handled"):
                if result.get("clear_pending"):
                    self._clear_pending(route_key)
                reply = result.get("message") or ""
                for chunk in text_chunks(reply, limit=_MAX_MSG):
                    await message.reply_text(chunk)
                return

        # Strip command prefix if present (e.g. /start → empty)
        parts = text.split(maxsplit=1) if text.startswith("/") else ["", text]
        body = parts[1].strip() if len(parts) > 1 else text

        running = self._get_running(route_key)
        if running is not None:
            self._queue_message(route_key, body)
            await message.reply_text("Queued after current analysis.")
            return

        task = asyncio.create_task(self._analysis_wrapper(route, update, body))
        self._set_task(route_key, task, body)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        logger.info("Telegram bot initializing")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        me = self._app.bot
        try:
            info = await me.get_me()
            logger.info("Telegram bot ready as @%s (%s)", info.username, info.id)
        except Exception as exc:
            logger.warning("Telegram get_me failed: %s", exc)
        # Register bot commands so Telegram's "/" menu matches PantheonClaw's actual commands
        try:
            from telegram import BotCommand
            await me.set_my_commands([
                BotCommand("menu", "Show command menu"),
                BotCommand("status", "Show routed chat status"),
                BotCommand("agents", "Show agents in current team"),
                BotCommand("agent", "Switch active agent"),
                BotCommand("team", "Show current team and usage"),
                BotCommand("model", "Show or set model"),
                BotCommand("new", "Start a fresh chat"),
                BotCommand("list", "List routed chats"),
                BotCommand("resume", "Resume a previous chat"),
                BotCommand("cancel", "Cancel running analysis"),
                BotCommand("reset", "Delete chat and start over"),
            ])
            logger.info("Telegram bot commands registered")
        except Exception as exc:
            logger.warning("Failed to register Telegram bot commands: %s", exc)
        try:
            await asyncio.to_thread(self._stop_event.wait)
        finally:
            logger.info("Telegram bot stopping")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()


async def run_telegram_channel(
    *,
    bridge: Any,
    config: dict[str, Any],
    stop_event: threading.Event,
) -> None:
    bot = TelegramGatewayBot(bridge=bridge, config=config, stop_event=stop_event)
    await bot.run()
