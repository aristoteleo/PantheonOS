from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from pantheon.settings import get_settings


ALL_CHANNELS: tuple[str, ...] = (
    "slack",
    "telegram",
    "discord",
    "wechat",
    "feishu",
    "qq",
    "imessage",
)

IMPLEMENTED_CHANNELS: tuple[str, ...] = (
    "slack",
    "telegram",
    "discord",
    "wechat",
    "feishu",
    "qq",
    "imessage",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "channel": None,
    "auto_start": [],
    "images": {
        "enabled": True,
        "max_size_bytes": 10 * 1024 * 1024,
        "max_dimension": 1568,
    },
    "slack": {
        "app_token": None,
        "bot_token": None,
    },
    "telegram": {
        "token": None,
        "allowed_users": [],
    },
    "discord": {
        "token": None,
    },
    "wechat": {
        "token": None,
        "base_url": "https://ilinkai.weixin.qq.com",
        "allow_from": [],
    },
    "feishu": {
        "app_id": None,
        "app_secret": None,
        "connection_mode": "websocket",
        "verification_token": None,
        "encrypt_key": None,
        "host": "0.0.0.0",
        "port": 8080,
        "path": "/feishu/events",
    },
    "imessage": {
        "cli_path": "imsg",
        "db_path": "~/Library/Messages/chat.db",
        "include_attachments": True,
    },
    "qq": {
        "app_id": None,
        "client_secret": None,
        "image_host": None,
        "image_server_port": 8081,
        "markdown": False,
    },
}

_SENSITIVE_FIELDS: tuple[tuple[str, str], ...] = (
    ("slack", "app_token"),
    ("slack", "bot_token"),
    ("telegram", "token"),
    ("discord", "token"),
    ("wechat", "token"),
    ("feishu", "app_secret"),
    ("feishu", "verification_token"),
    ("feishu", "encrypt_key"),
    ("qq", "client_secret"),
)


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def default_config_path() -> Path:
    return get_settings().pantheon_dir / "claw" / "config.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _looks_masked(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    # Fully masked: all * or •
    if set(stripped) <= {"*", "•"}:
        return True
    # Partially masked: _mask_secret format is first2 + stars + last2
    # e.g. "jL************qG" — middle is all *
    if len(stripped) >= 5 and set(stripped[2:-2]) <= {"*"}:
        return True
    return False


def _mask_secret(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}{'*' * (len(text) - 4)}{text[-2:]}"


class ClawConfigStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_path()

    def load(self) -> dict[str, Any]:
        cfg = default_config()
        if not self.path.exists():
            return cfg
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return cfg
        return _deep_merge(cfg, raw)

    def save(self, config: dict[str, Any]) -> dict[str, Any]:
        existing = self.load()
        merged = _deep_merge(default_config(), config or {})
        for section, field in _SENSITIVE_FIELDS:
            incoming = ((merged.get(section) or {}).get(field))
            if not incoming or _looks_masked(incoming):
                old_value = ((existing.get(section) or {}).get(field))
                if old_value:
                    merged.setdefault(section, {})[field] = old_value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return merged

    def load_masked(self) -> dict[str, Any]:
        cfg = self.load()
        masked = copy.deepcopy(cfg)
        for section, field in _SENSITIVE_FIELDS:
            section_data = masked.get(section)
            if isinstance(section_data, dict):
                section_data[field] = _mask_secret(section_data.get(field))
        return masked
