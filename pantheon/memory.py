import json
from pathlib import Path
from uuid import uuid4

from .utils.llm import process_messages_for_store
from .utils.log import logger


# FIX: other memory implimentaion?
class Memory:
    """
    The Memory class is used to store the memory of the agent.

    Args:
        name: The name of the memory.

    Attributes:
        name: The name of the memory.
        id: The ID of the memory.
        extra_data: The extra data of the memory.
    """

    def __init__(self, name: str):
        self.name = name
        self.id = str(uuid4())
        self._messages: list[dict] = []
        self.extra_data: dict = {}

    def save(self, file_path: str):
        """
        Save the memory to a file.

        Args:
            file_path: The path to save the memory to.
        """
        with open(file_path, "w") as f:
            json.dump(
                {
                    "id": self.id,
                    "name": self.name,
                    "messages": self._messages,
                    "extra_data": self.extra_data,
                },
                f,
            )

    @classmethod
    def load(cls, file_path: str):
        """
        Load the memory from a file.

        Args:
            file_path: The path to load the memory from.
        """
        with open(file_path, "r") as f:
            data = json.load(f)
            memory = cls(data["name"])
            memory.id = data["id"]
            memory._messages = data["messages"]
            memory.extra_data = data.get("extra_data", {})
            return memory

    def add_messages(self, messages: list[dict]):
        """
        Add messages to the memory.

        Args:
            messages: The messages to add to the memory.
        """
        messages = process_messages_for_store(messages)
        self._messages.extend(messages)

    def get_messages(self):
        """
        Get the messages from the memory.

        Returns:
            The messages from the memory.
        """
        return self._messages

    def cleanup(self):
        """Cleanup the memory after the agent is interrupted."""
        while True:
            if len(self._messages) == 0:
                break

            last_message = self._messages[-1]
            if "role" not in last_message:
                logger.debug(
                    f"Popping message: {last_message}, len={len(self._messages)}"
                )
                self._messages.pop()
                continue
            if last_message["role"] == "user":
                logger.debug(
                    f"Popping user message: {last_message}, len={len(self._messages)}"
                )
                self._messages.pop()
                continue
            if last_message.get("content") is None:
                logger.debug(
                    f"Popping message: {last_message}, len={len(self._messages)}"
                )
                self._messages.pop()
                continue
            if last_message["role"] == "assistant":
                if last_message["tool_calls"]:
                    last_message["tool_calls"] = None
            if last_message["role"] == "assistant":
                break
            if last_message["role"] == "tool":
                break


DEFAULT_CHAT_NAME = "New Chat"


class MemoryManager:
    """
    The MemoryManager class is used to manage multiple memories.

    Args:
        path: The path to the memory files.

    Attributes:
        path: The path to the memory files.
        memory_store: The in-RAM store of the memories.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.memory_store: dict[str, Memory] = {}
        self.load()

    def new_memory(self, name: str | None = None) -> Memory:
        """
        Create a new memory.

        Args:
            name: The name of the memory.
        """
        if name is None:
            name = DEFAULT_CHAT_NAME
        memory = Memory(name)
        self.memory_store[memory.id] = memory
        return memory

    def get_memory(self, id: str) -> Memory:
        """
        Get a memory by its ID.

        Args:
            id: The ID of the memory.
        """
        return self.memory_store[id]

    def delete_memory(self, id: str):
        """
        Delete a memory by its ID.

        Args:
            id: The ID of the memory.
        """
        del self.memory_store[id]

    def list_memories(self):
        """
        List all the memories.

        Returns:
            The list of the memories.
        """
        return list(self.memory_store.keys())

    def save(self):
        """
        Save all the memories to the file system.
        """
        for memory in self.memory_store.values():
            memory.save(str(self.path / f"{memory.id}.json"))
        for file in self.path.glob("*.json"):
            if file.stem not in self.memory_store:
                file.unlink()

    def load(self):
        """
        Load all the memories from the file system.
        """
        if not self.path.exists():
            self.path.mkdir(parents=True)
        for file in self.path.glob("*.json"):
            try:
                memory = Memory.load(str(file))
                logger.info(f"Loaded memory: {memory.name} from {file}")
                self.memory_store[memory.id] = memory
                if memory.extra_data.get("running"):
                    memory.extra_data["running"] = False
            except Exception as e:
                logger.error(f"Failed to load memory from {file}: {e}")

    def update_memory_name(self, memory_id: str, name: str):
        """
        Update the name of a memory.

        Args:
            memory_id: The ID of the memory.
            name: The new name of the memory.
        """
        memory = self.get_memory(memory_id)
        memory.name = name
        self.save()
