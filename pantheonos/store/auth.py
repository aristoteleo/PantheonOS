"""Store authentication - manages JWT token storage for CLI."""

import json
from pathlib import Path
from typing import Optional


AUTH_FILE = Path.home() / ".pantheon" / "store_auth.json"


class StoreAuth:
    """Manage store authentication credentials."""

    def __init__(self):
        self._data: dict = {}
        self._load()

    def _load(self):
        if AUTH_FILE.exists():
            try:
                self._data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self, hub_url: str, access_token: str, username: str, user_id: str = ""):
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._data = {
            "hub_url": hub_url,
            "access_token": access_token,
            "username": username,
            "user_id": user_id,
        }
        AUTH_FILE.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        # Set restrictive permissions
        try:
            AUTH_FILE.chmod(0o600)
        except OSError:
            pass  # Windows may not support chmod

    def clear(self):
        if AUTH_FILE.exists():
            AUTH_FILE.unlink()
        self._data = {}

    @property
    def token(self) -> Optional[str]:
        return self._data.get("access_token")

    @property
    def hub_url(self) -> Optional[str]:
        return self._data.get("hub_url")

    @property
    def username(self) -> Optional[str]:
        return self._data.get("username")

    @property
    def is_logged_in(self) -> bool:
        return bool(self._data.get("access_token"))
