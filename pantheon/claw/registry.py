from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pantheon.settings import get_settings


@dataclass(frozen=True)
class ConversationRoute:
    channel: str
    scope_type: str
    scope_id: str
    thread_id: str | None = None
    sender_id: str | None = None

    def route_key(self) -> str:
        if self.thread_id:
            return f"{self.channel}:{self.scope_type}:{self.scope_id}:thread:{self.thread_id}"
        return f"{self.channel}:{self.scope_type}:{self.scope_id}"

    @property
    def is_direct(self) -> bool:
        return self.scope_type in {"dm", "direct", "private", "p2p"}

    @property
    def stable_short_id(self) -> str:
        return hashlib.sha1(self.route_key().encode("utf-8")).hexdigest()[:12]


def default_registry_path() -> Path:
    return get_settings().pantheon_dir / "claw" / "routes.json"


class ClawRouteRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_registry_path()
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict):
            self._entries = data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, route: ConversationRoute) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(route.route_key())
            return dict(entry) if entry is not None else None

    def set(
        self,
        route: ConversationRoute,
        *,
        chat_id: str,
        chat_name: str,
    ) -> dict[str, Any]:
        with self._lock:
            entry = {
                **asdict(route),
                "route_key": route.route_key(),
                "chat_id": chat_id,
                "chat_name": chat_name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._entries[route.route_key()] = entry
            self._save()
            return dict(entry)

    def touch(self, route: ConversationRoute) -> None:
        with self._lock:
            entry = self._entries.get(route.route_key())
            if entry is None:
                return
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def remove(self, route: ConversationRoute) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.pop(route.route_key(), None)
            self._save()
            return dict(entry) if entry is not None else None

    def list_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(value)
                for value in sorted(
                    self._entries.values(),
                    key=lambda item: item.get("updated_at", ""),
                    reverse=True,
                )
            ]
