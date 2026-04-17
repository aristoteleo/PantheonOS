"""Storage backends for Memory persistence."""

import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .memory import Memory


class StorageBackend(ABC):
    """Abstract base class for memory storage backends."""

    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._last_persisted_count: Dict[str, int] = {}
        self._write_locks: Dict[str, threading.RLock] = {}
        self._write_locks_guard = threading.Lock()

    def _memory_lock(self, memory_id: str) -> threading.RLock:
        with self._write_locks_guard:
            lock = self._write_locks.get(memory_id)
            if lock is None:
                lock = threading.RLock()
                self._write_locks[memory_id] = lock
            return lock

    def persist(self, memory: "Memory") -> None:
        """Intelligently persist memory (auto-decides append vs rewrite)."""
        memory_id = memory.id
        with self._memory_lock(memory_id):
            current_count = len(memory._messages)
            last_count = self._last_persisted_count.get(memory_id, 0)

            self.save_metadata(memory_id, memory.name, memory.extra_data)

            if current_count > last_count:
                new_messages = memory._messages[last_count:]
                self.append_messages(memory_id, new_messages)
            elif current_count != last_count:
                self.rewrite_messages(memory_id, memory._messages)

            self._last_persisted_count[memory_id] = current_count

    def initialize_tracking(self, memory_id: str, message_count: int) -> None:
        """Initialize message count tracking for a loaded memory."""
        self._last_persisted_count[memory_id] = message_count

    @abstractmethod
    def save_metadata(self, memory_id: str, name: str, extra_data: dict) -> None:
        pass

    @abstractmethod
    def load_metadata(self, memory_id: str) -> Tuple[str, str, dict]:
        pass

    @abstractmethod
    def append_messages(self, memory_id: str, messages: List[dict]) -> None:
        pass

    @abstractmethod
    def load_messages(self, memory_id: str) -> List[dict]:
        pass

    @abstractmethod
    def rewrite_messages(self, memory_id: str, messages: List[dict]) -> None:
        pass

    @abstractmethod
    def exists(self, memory_id: str) -> bool:
        pass

    @abstractmethod
    def delete(self, memory_id: str) -> None:
        pass

    @classmethod
    @abstractmethod
    def detect_format(cls, base_path: Path, memory_id: str) -> bool:
        pass

    def _atomic_write_json(self, path: Path, data: dict) -> None:
        """Write JSON atomically so interrupted writes don't corrupt existing files."""
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            tmp_path.replace(path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)


class JSONBackend(StorageBackend):
    """Legacy JSON format: single {id}.json file."""

    def persist(self, memory: "Memory") -> None:
        file_path = self.base_path / f"{memory.id}.json"
        with self._memory_lock(memory.id):
            self._atomic_write_json(
                file_path,
                {
                    "id": memory.id,
                    "name": memory.name,
                    "messages": memory._messages,
                    "extra_data": memory.extra_data,
                },
            )
            self._last_persisted_count[memory.id] = len(memory._messages)

    def _get_file_path(self, memory_id: str) -> Path:
        return self.base_path / f"{memory_id}.json"

    @classmethod
    def detect_format(cls, base_path: Path, memory_id: str) -> bool:
        json_file = base_path / f"{memory_id}.json"
        meta_file = base_path / f"{memory_id}.meta.json"
        return json_file.exists() and not meta_file.exists()

    def save_metadata(self, memory_id: str, name: str, extra_data: dict) -> None:
        with self._memory_lock(memory_id):
            file_path = self._get_file_path(memory_id)
            messages = []
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    messages = json.load(f).get("messages", [])
            self._atomic_write_json(
                file_path,
                {
                    "id": memory_id,
                    "name": name,
                    "messages": messages,
                    "extra_data": extra_data,
                },
            )

    def load_metadata(self, memory_id: str) -> Tuple[str, str, dict]:
        with open(self._get_file_path(memory_id), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data["id"], data["name"], data.get("extra_data", {})

    def append_messages(self, memory_id: str, messages: List[dict]) -> None:
        with self._memory_lock(memory_id):
            file_path = self._get_file_path(memory_id)
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {"id": memory_id, "name": "New Chat", "messages": [], "extra_data": {}}
            data["messages"].extend(messages)
            self._atomic_write_json(file_path, data)

    def load_messages(self, memory_id: str) -> List[dict]:
        with open(self._get_file_path(memory_id), "r", encoding="utf-8") as f:
            return json.load(f).get("messages", [])

    def rewrite_messages(self, memory_id: str, messages: List[dict]) -> None:
        with self._memory_lock(memory_id):
            file_path = self._get_file_path(memory_id)
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {"id": memory_id, "name": "New Chat", "extra_data": {}}
            data["messages"] = messages
            self._atomic_write_json(file_path, data)

    def exists(self, memory_id: str) -> bool:
        return self._get_file_path(memory_id).exists()

    def delete(self, memory_id: str) -> None:
        with self._memory_lock(memory_id):
            file_path = self._get_file_path(memory_id)
            if file_path.exists():
                file_path.unlink()


class JSONLBackend(StorageBackend):
    """High-performance JSONL format: {id}.meta.json + {id}.jsonl."""

    def persist(self, memory: "Memory") -> None:
        super().persist(memory)

    def _get_meta_path(self, memory_id: str) -> Path:
        return self.base_path / f"{memory_id}.meta.json"

    def _get_jsonl_path(self, memory_id: str) -> Path:
        return self.base_path / f"{memory_id}.jsonl"

    @classmethod
    def detect_format(cls, base_path: Path, memory_id: str) -> bool:
        return (base_path / f"{memory_id}.meta.json").exists()

    def save_metadata(self, memory_id: str, name: str, extra_data: dict) -> None:
        with self._memory_lock(memory_id):
            self._atomic_write_json(
                self._get_meta_path(memory_id),
                {
                    "id": memory_id,
                    "name": name,
                    "extra_data": extra_data,
                },
            )

    def load_metadata(self, memory_id: str) -> Tuple[str, str, dict]:
        with open(self._get_meta_path(memory_id), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data["id"], data["name"], data.get("extra_data", {})

    def append_messages(self, memory_id: str, messages: List[dict]) -> None:
        with self._memory_lock(memory_id):
            with open(self._get_jsonl_path(memory_id), "a", encoding="utf-8") as f:
                for msg in messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def load_messages(self, memory_id: str) -> List[dict]:
        jsonl_path = self._get_jsonl_path(memory_id)
        if not jsonl_path.exists():
            return []
        messages = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
        return messages

    def rewrite_messages(self, memory_id: str, messages: List[dict]) -> None:
        with self._memory_lock(memory_id):
            with open(self._get_jsonl_path(memory_id), "w", encoding="utf-8") as f:
                for msg in messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def exists(self, memory_id: str) -> bool:
        return self._get_meta_path(memory_id).exists()

    def delete(self, memory_id: str) -> None:
        with self._memory_lock(memory_id):
            for path in [self._get_meta_path(memory_id), self._get_jsonl_path(memory_id)]:
                if path.exists():
                    path.unlink()
