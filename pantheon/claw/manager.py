from __future__ import annotations

import collections
import importlib
import logging
import threading
from typing import Any, Callable

from .bridge import ChatRoomGatewayBridge
from .config import ALL_CHANNELS, IMPLEMENTED_CHANNELS, ClawConfigStore
from .registry import ClawRouteRegistry

from pantheon.utils.log import logger

_LOG_BUFFER_SIZE = 200
_RUNNER_SPECS: dict[str, tuple[str, str]] = {
    "slack": ("pantheon.claw.channels.slack", "run_slack_channel"),
    "telegram": ("pantheon.claw.channels.telegram", "run_telegram_channel"),
    "discord": ("pantheon.claw.channels.discord", "run_discord_channel"),
    "wechat": ("pantheon.claw.channels.wechat", "run_wechat_channel"),
    "feishu": ("pantheon.claw.channels.feishu", "run_feishu_channel"),
    "qq": ("pantheon.claw.channels.qq", "run_qq_channel"),
    "imessage": ("pantheon.claw.channels.imessage", "run_imessage_channel"),
}


def _channel_configured(channel: str, cfg: dict[str, Any]) -> bool:
    if channel == "slack":
        sc = cfg.get("slack") or {}
        return bool(sc.get("app_token") and sc.get("bot_token"))
    if channel == "telegram":
        return bool((cfg.get("telegram") or {}).get("token"))
    if channel == "discord":
        return bool((cfg.get("discord") or {}).get("token"))
    if channel == "wechat":
        return bool((cfg.get("wechat") or {}).get("token"))
    if channel == "feishu":
        fc = cfg.get("feishu") or {}
        return bool(fc.get("app_id") and fc.get("app_secret"))
    if channel == "qq":
        qc = cfg.get("qq") or {}
        return bool(qc.get("app_id") and qc.get("client_secret"))
    if channel == "imessage":
        ic = cfg.get("imessage") or {}
        return bool(ic.get("cli_path") or ic.get("db_path"))
    return False


class _ChannelLogHandler(logging.Handler):
    def __init__(self, *, channel: str, buffer: collections.deque[str]) -> None:
        super().__init__()
        self._channel = channel
        self._buffer = buffer
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        name = record.name or ""
        if not name.startswith(f"pantheon.claw.channels.{self._channel}"):
            return
        self._buffer.append(self.format(record) + "\n")


class GatewayChannelManager:
    def __init__(self, *, chatroom: Any, loop, config_store: ClawConfigStore | None = None, registry: ClawRouteRegistry | None = None) -> None:
        self._chatroom = chatroom
        self._loop = loop
        self._config_store = config_store or ClawConfigStore()
        self._registry = registry or ClawRouteRegistry()
        self._bridge = ChatRoomGatewayBridge(chatroom=chatroom, registry=self._registry, loop=loop)
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._logs: dict[str, collections.deque[str]] = {}
        self._handlers: dict[str, logging.Handler] = {}

    def get_config(self, *, masked: bool = True) -> dict[str, Any]:
        return self._config_store.load_masked() if masked else self._config_store.load()

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return self._config_store.save(config)

    def list_states(self) -> list[dict[str, Any]]:
        cfg = self._config_store.load()
        snapshot: list[dict[str, Any]] = []
        with self._lock:
            for channel in ALL_CHANNELS:
                configured = _channel_configured(channel, cfg)
                state = dict(self._states.get(channel, {}))
                if not state:
                    state = {
                        "channel": channel,
                        "status": "stopped" if configured else "not_configured",
                        "running": False,
                    }
                state["configured"] = configured
                state["supported"] = channel in IMPLEMENTED_CHANNELS
                state["can_start"] = (
                    configured
                    and state["supported"]
                    and state.get("status") not in {"running", "starting"}
                )
                state["mode"] = "thread"
                state["log_lines"] = len(self._logs.get(channel, []))
                snapshot.append(state)
        return snapshot

    def start_channel(self, channel: str, *, source: str = "manual") -> dict[str, Any]:
        cfg = self._config_store.load()
        if channel not in IMPLEMENTED_CHANNELS:
            self._set_state(channel, status="unsupported", running=False, error=f"{channel} is not implemented in PantheonClaw yet")
            return {"ok": False, "error": f"{channel} is not implemented in PantheonClaw yet"}
        if not _channel_configured(channel, cfg):
            self._set_state(channel, status="failed", running=False, error=f"{channel} is not configured")
            return {"ok": False, "error": f"{channel} is not configured"}
        runner, load_error = self._load_runner(channel)
        if runner is None:
            # Ensure a log buffer exists so the error is visible in the UI
            with self._lock:
                buf = self._logs.setdefault(channel, collections.deque(maxlen=_LOG_BUFFER_SIZE))
                buf.append(f"[error] {load_error}\n")
            self._set_state(channel, status="failed", running=False, error=load_error)
            return {"ok": False, "error": load_error}

        with self._lock:
            existing = self._threads.get(channel)
            if existing is not None and existing.is_alive():
                return {"ok": False, "error": f"{channel} is already running"}

            stop_event = threading.Event()
            log_buffer = collections.deque(maxlen=_LOG_BUFFER_SIZE)
            handler = _ChannelLogHandler(channel=channel, buffer=log_buffer)
            self._logs[channel] = log_buffer
            self._handlers[channel] = handler
            # Ensure INFO+ from this channel's logger reaches the handler
            # regardless of the root logger's level (default WARNING).
            channel_logger = logging.getLogger(f"pantheon.claw.channels.{channel}")
            channel_logger.setLevel(logging.DEBUG)
            logging.getLogger().addHandler(handler)

            def _runner() -> None:
                try:
                    import asyncio

                    asyncio.run(
                        runner(
                            bridge=self._bridge,
                            config=cfg.get(channel) or {},
                            stop_event=stop_event,
                        )
                    )
                    final_status = "stopped" if stop_event.is_set() else "failed"
                    self._set_state(channel, status=final_status, running=False)
                except Exception as exc:
                    log_buffer.append(f"{channel} failed: {exc}\n")
                    self._set_state(channel, status="failed", running=False, error=str(exc))
                    logger.exception("Channel %s crashed", channel)
                finally:
                    logging.getLogger().removeHandler(handler)
                    logging.getLogger(f"pantheon.claw.channels.{channel}").setLevel(logging.NOTSET)
                    with self._lock:
                        self._threads.pop(channel, None)
                        self._stop_events.pop(channel, None)
                        self._handlers.pop(channel, None)

            thread = threading.Thread(target=_runner, daemon=True, name=f"pantheon-claw-{channel}")
            self._threads[channel] = thread
            self._stop_events[channel] = stop_event
            self._states[channel] = {
                "channel": channel,
                "status": "running",
                "running": True,
                "desired_state": "running",
                "source": source,
                "mode": "thread",
                "thread_name": thread.name,
            }
            log_buffer.append(f"[start] channel={channel} source={source}\n")
            thread.start()
            return {"ok": True, "message": f"Started {channel}", "thread_name": thread.name}

    def stop_channel(self, channel: str) -> dict[str, Any]:
        with self._lock:
            stop_event = self._stop_events.get(channel)
            thread = self._threads.get(channel)
        if stop_event is None or thread is None or not thread.is_alive():
            self._set_state(channel, status="stopped", running=False, desired_state="stopped")
            return {"ok": False, "error": f"{channel} is not running"}
        stop_event.set()
        self._set_state(channel, status="stopping", running=False, desired_state="stopped")
        return {"ok": True, "message": f"Stopping {channel}"}

    def stop_all(self) -> list[dict[str, Any]]:
        return [self.stop_channel(channel) for channel in ALL_CHANNELS]

    def get_logs(self, channel: str) -> str:
        return "".join(self._logs.get(channel, []))

    def wechat_get_login_qr(self) -> dict[str, Any]:
        """Request a WeChat QR code for bot login. Returns qrcode_id and a base64 PNG."""
        import base64 as _b64
        import secrets

        import requests as _req

        cfg = self._config_store.load()
        wechat_cfg = cfg.get("wechat") or {}
        base_url = str(wechat_cfg.get("base_url") or "https://ilinkai.weixin.qq.com").rstrip("/")
        _wechat_log = logging.getLogger("pantheon.claw.channels.wechat")
        with self._lock:
            buf = self._logs.setdefault("wechat", collections.deque(maxlen=_LOG_BUFFER_SIZE))
        buf.append(f"[wechat_login_qr] GET {base_url}/ilink/bot/get_bot_qrcode\n")
        _wechat_log.info("wechat_get_login_qr: GET %s/ilink/bot/get_bot_qrcode", base_url)
        uin = _b64.b64encode(str(secrets.randbits(32)).encode()).decode("ascii")
        headers = {"AuthorizationType": "ilink_bot_token", "X-WECHAT-UIN": uin}
        try:
            resp = _req.get(
                f"{base_url}/ilink/bot/get_bot_qrcode",
                params={"bot_type": "3"},
                headers=headers,
                timeout=(10, 30),
            )
            _wechat_log.info("wechat_get_login_qr: HTTP %s", resp.status_code)
            buf.append(f"[wechat_login_qr] HTTP {resp.status_code}\n")
            resp.raise_for_status()
            data = resp.json()
            buf.append(f"[wechat_login_qr] response keys={list(data.keys())}\n")
            _wechat_log.info("wechat_get_login_qr: response keys=%s", list(data.keys()))
        except Exception as exc:
            buf.append(f"[wechat_login_qr] request failed: {exc}\n")
            _wechat_log.error("wechat_get_login_qr: request failed: %s", exc)
            raise

        qr_id = str(data.get("qrcode") or "").strip()
        qr_url = str(data.get("qrcode_img_content") or "").strip()
        buf.append(f"[wechat_login_qr] qr_id={qr_id!r} qr_url={qr_url[:80]!r}\n")
        _wechat_log.info("wechat_get_login_qr: qr_id=%r qr_url=%r", qr_id, qr_url[:80] if qr_url else "")
        if not qr_id:
            raise RuntimeError(f"WeChat did not return a qrcode id: {data}")

        qr_image: str | None = None
        if qr_url:
            try:
                import io
                import qrcode as _qrcode  # type: ignore[import]

                img = _qrcode.make(qr_url)
                img_buf = io.BytesIO()
                img.save(img_buf, format="PNG")
                qr_image = "data:image/png;base64," + _b64.b64encode(img_buf.getvalue()).decode()
                buf.append(f"[wechat_login_qr] QR image generated ({len(img_buf.getvalue())} bytes)\n")
                _wechat_log.info("wechat_get_login_qr: QR image generated (%d bytes)", len(img_buf.getvalue()))
            except ImportError:
                buf.append("[wechat_login_qr] qrcode library not installed, returning URL only\n")
                _wechat_log.warning("wechat_get_login_qr: qrcode library not installed, returning URL only")
            except Exception as exc:
                buf.append(f"[wechat_login_qr] QR image generation failed: {exc}\n")
                _wechat_log.error("wechat_get_login_qr: QR image generation failed: %s", exc)

        return {
            "qrcode_id": qr_id,
            "qrcode_url": qr_url,
            "qrcode_image": qr_image,
            "message": data.get("message"),
        }

    def wechat_poll_login_status(self, qrcode_id: str) -> dict[str, Any]:
        """Poll WeChat QR login status. On confirmed, saves bot_token to config."""
        import base64 as _b64
        import secrets

        import requests as _req

        cfg = self._config_store.load()
        wechat_cfg = cfg.get("wechat") or {}
        base_url = str(wechat_cfg.get("base_url") or "https://ilinkai.weixin.qq.com").rstrip("/")
        _wechat_log = logging.getLogger("pantheon.claw.channels.wechat")
        with self._lock:
            _poll_buf = self._logs.setdefault("wechat", collections.deque(maxlen=_LOG_BUFFER_SIZE))
        _wechat_log.debug("wechat_poll_login_status: qrcode_id=%r", qrcode_id)
        uin = _b64.b64encode(str(secrets.randbits(32)).encode()).decode("ascii")
        headers = {"AuthorizationType": "ilink_bot_token", "X-WECHAT-UIN": uin}
        try:
            resp = _req.get(
                f"{base_url}/ilink/bot/get_qrcode_status",
                params={"qrcode": str(qrcode_id or "")},
                headers=headers,
                timeout=(10, 40),
            )
            resp.raise_for_status()
            data = resp.json()
            _wechat_log.info("wechat_poll_login_status: status=%r", data.get("status"))
            _poll_buf.append(f"[wechat_login_status] status={data.get('status')!r}\n")
        except _req.exceptions.ReadTimeout:
            _wechat_log.debug("wechat_poll_login_status: read timeout, treating as wait")
            return {"ok": True, "status": "wait", "bot_token": None}
        except Exception as exc:
            _poll_buf.append(f"[wechat_login_status] request failed: {exc}\n")
            _wechat_log.error("wechat_poll_login_status: request failed: %s", exc)
            raise
        status = str(data.get("status") or "").lower()
        bot_token = str(data.get("bot_token") or "").strip()
        if status == "confirmed" and bot_token:
            updated = dict(cfg)
            updated["wechat"] = dict(wechat_cfg)
            updated["wechat"]["token"] = bot_token
            base_url_from_resp = str(data.get("baseurl") or "").strip()
            if base_url_from_resp:
                updated["wechat"]["base_url"] = base_url_from_resp
            self._config_store.save(updated)
        return {
            "ok": True,
            "status": status or "wait",
            "bot_token": bot_token if status == "confirmed" else None,
            "baseurl": data.get("baseurl") if status == "confirmed" else None,
            "message": data.get("message", ""),
        }

    async def list_sessions(self) -> list[dict[str, Any]]:
        return await self._bridge.list_sessions()

    def _load_runner(self, channel: str) -> tuple[Callable[..., Any] | None, str | None]:
        module_name, attr_name = _RUNNER_SPECS[channel]
        try:
            module = importlib.import_module(module_name)
            return getattr(module, attr_name), None
        except ModuleNotFoundError as exc:
            missing = exc.name or "dependency"
            return None, f"Missing optional dependency '{missing}'. Install pantheon-agents[claw]."
        except Exception as exc:
            return None, f"Failed to load {channel} runner: {exc}"

    def _set_state(self, channel: str, **fields: Any) -> None:
        with self._lock:
            state = dict(self._states.get(channel, {}))
            state.update(fields)
            state.setdefault("channel", channel)
            self._states[channel] = state
