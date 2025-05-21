import json
from pathlib import Path
from uuid import uuid4

from .utils.llm import process_messages_for_store
from .utils.log import logger


class Memory:
    def __init__(self, name: str):
        self.name = name
        self.id = str(uuid4())
        self._messages: list[dict] = []
        self.extra_data: dict = {}

    def save(self, file_path: str):
        with open(file_path, "w") as f:
            json.dump({
                "id": self.id,
                "name": self.name,
                "messages": self._messages,
                "extra_data": self.extra_data,
            }, f)

    @classmethod
    def load(cls, file_path: str):
        with open(file_path, "r") as f:
            data = json.load(f)
            memory = cls(data["name"])
            memory.id = data["id"]
            memory._messages = data["messages"]
            memory.extra_data = data.get("extra_data", {})
            return memory

    def add_messages(self, messages: list[dict]):
        messages = process_messages_for_store(messages)
        self._messages.extend(messages)

    def get_messages(self):
        return self._messages


DEFAULT_CHAT_NAME = "New Chat"


class MemoryManager:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.memory_store: dict[str, Memory] = {}
        self.load()

    def new_memory(self, name: str | None = None) -> Memory:
        if name is None:
            name = DEFAULT_CHAT_NAME
        memory = Memory(name)
        self.memory_store[memory.id] = memory
        return memory

    def get_memory(self, id: str) -> Memory:
        return self.memory_store[id]

    def delete_memory(self, id: str):
        del self.memory_store[id]

    def list_memories(self):
        return list(self.memory_store.keys())

    def save(self):
        for memory in self.memory_store.values():
            memory.save(str(self.path / f"{memory.id}.json"))
        for file in self.path.glob("*.json"):
            if file.stem not in self.memory_store:
                file.unlink()

    def load(self):
        if not self.path.exists():
            self.path.mkdir(parents=True)
        for file in self.path.glob("*.json"):
            memory = Memory.load(str(file))
            logger.info(f"Loaded memory: {memory.name} from {file}")
            self.memory_store[memory.id] = memory

    def update_memory_name(self, memory_id: str, name: str):
        memory = self.get_memory(memory_id)
        memory.name = name
        self.save()
