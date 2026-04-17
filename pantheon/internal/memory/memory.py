import asyncio
import json
from pathlib import Path
from uuid import uuid4

from .storage import StorageBackend, JSONBackend, JSONLBackend
from pantheon.utils.llm import process_messages_for_store
from pantheon.utils.log import logger

_ALL_CONTEXTS = object()


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

    def __init__(
        self,
        name: str,
        file_path: str | None = None,
        persist_delay: float = 2.0,
        storage_backend: StorageBackend | None = None,
        use_jsonl: bool = True,
    ):
        self.name = name
        self.id = str(uuid4())
        self._messages: list[dict] = []
        self.extra_data: dict[str, object] = {}

        # Debounced persistence (opt-in: only when file_path is set)
        self._file_path: str | None = file_path
        self._persist_delay = persist_delay
        self._persist_task: asyncio.Task | None = None

        # Storage backend routing (auto-detect format)
        self._backend: StorageBackend | None = None
        self._use_jsonl = use_jsonl

        if storage_backend:
            # Explicit backend injection
            self._backend = storage_backend
        elif file_path:
            # Auto-detect format based on existing files
            base_path = Path(file_path).parent
            memory_id = Path(file_path).stem

            if JSONLBackend.detect_format(base_path, memory_id):
                self._backend = JSONLBackend(base_path)
                logger.debug(f"Detected JSONL format for memory {memory_id}")
            elif JSONBackend.detect_format(base_path, memory_id):
                self._backend = JSONBackend(base_path)
                logger.debug(f"Detected JSON format for memory {memory_id}")
            else:
                # New memory: use configured format
                if use_jsonl:
                    self._backend = JSONLBackend(base_path)
                    logger.debug(f"Using JSONL format for new memory {memory_id}")
                else:
                    self._backend = JSONBackend(base_path)
                    logger.debug(f"Using JSON format for new memory {memory_id}")

    def __getitem__(self, key: int | slice):
        """Get a message or slice of messages from the memory."""
        if isinstance(key, int):
            return self._messages[key]
        elif isinstance(key, slice):
            new_memory = Memory(self.name)
            new_memory._messages = self._messages[key]
            new_memory.extra_data = self.extra_data.copy()
            return new_memory
        else:
            raise ValueError(f"Invalid key: {key}")

    @property
    def file_path(self) -> str | None:
        """
        Get the primary file path for this memory.

        For JSON backend: returns {id}.json
        For JSONL backend: returns {id}.jsonl (messages file)
        For no backend: returns _file_path

        Returns:
            File path string, or None if no backend/file_path configured
        """
        if self._backend:
            if isinstance(self._backend, JSONLBackend):
                return str(self._backend._get_jsonl_path(self.id))
            elif isinstance(self._backend, JSONBackend):
                return str(self._backend._get_file_path(self.id))

        return self._file_path

    @property
    def storage_files(self) -> list[str]:
        """
        Get all storage files for this memory.

        For JSON backend: returns [{id}.json]
        For JSONL backend: returns [{id}.meta.json, {id}.jsonl]
        For no backend: returns [_file_path] if set

        Returns:
            List of file paths
        """
        if self._backend:
            if isinstance(self._backend, JSONLBackend):
                return [
                    str(self._backend._get_meta_path(self.id)),
                    str(self._backend._get_jsonl_path(self.id)),
                ]
            elif isinstance(self._backend, JSONBackend):
                return [str(self._backend._get_file_path(self.id))]

        if self._file_path:
            return [self._file_path]

        return []

    def save(self, file_path: str | None = None):
        """
        Save the memory to a file.

        Args:
            file_path: The path to save the memory to. If None and backend is available,
                      uses backend's default location. If specified, always saves as
                      legacy JSON format (single file, portable).
        """
        if file_path:
            # Explicit file path: always save as legacy JSON format (portable, single file)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "id": self.id,
                        "name": self.name,
                        "messages": self._messages,
                        "extra_data": self.extra_data,
                    },
                    f,
                    indent=4,
                )
        elif self._backend:
            # Use backend's intelligent persistence
            self._backend.persist(self)
        else:
            raise ValueError("No file_path provided and no backend configured")

    @classmethod
    def load(cls, file_path: str, use_jsonl: bool = True):
        """
        Load the memory from a file (auto-detects format).

        Args:
            file_path: The path to load the memory from.
            use_jsonl: Default format for new memories (not used for existing files).
        """
        file_path_obj = Path(file_path)
        base_path = file_path_obj.parent
        memory_id = file_path_obj.stem

        # Auto-detect format
        if JSONLBackend.detect_format(base_path, memory_id):
            backend = JSONLBackend(base_path)
            memory_id, name, extra_data = backend.load_metadata(memory_id)
            messages = backend.load_messages(memory_id)

            memory = cls(name, file_path=str(file_path), use_jsonl=use_jsonl)
            memory.id = memory_id
            memory._messages = messages
            memory.extra_data = extra_data
            memory._backend = backend
            # Initialize tracking for loaded memory
            backend.initialize_tracking(memory_id, len(messages))
            return memory

        elif JSONBackend.detect_format(base_path, memory_id):
            backend = JSONBackend(base_path)
            memory_id, name, extra_data = backend.load_metadata(memory_id)
            messages = backend.load_messages(memory_id)

            memory = cls(name, file_path=str(file_path), use_jsonl=use_jsonl)
            memory.id = memory_id
            memory._messages = messages
            memory.extra_data = extra_data
            memory._backend = backend
            # Initialize tracking for loaded memory
            backend.initialize_tracking(memory_id, len(messages))
            return memory

        else:
            # Fallback to legacy JSON loading
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                memory = cls(data["name"], file_path=str(file_path), use_jsonl=use_jsonl)
                memory.id = data["id"]
                memory._messages = data["messages"]
                memory.extra_data = data.get("extra_data", {})
                return memory

    def clear(self):
        """
        Clear the memory.
        """
        self._messages = []
        self.extra_data = {}

    def add_messages(self, messages: list[dict]):
        """
        Add messages to the memory.

        Args:
            messages: The messages to add to the memory.
        """
        messages = process_messages_for_store(messages)
        self._messages.extend(messages)

        # Schedule persistence
        self._schedule_persist()

    def mark_dirty(self):
        """Mark memory as dirty to trigger delayed persistence.
        
        Call this after modifying extra_data or other fields that don't
        automatically trigger persistence (unlike add_messages).
        """
        self._schedule_persist()

    def set_metadata(self, key: str, value: object) -> bool:
        """Set a metadata key and schedule debounced persistence if it changed."""
        if self.extra_data.get(key) == value:
            return False
        self.extra_data[key] = value
        self.mark_dirty()
        return True

    def set_metadata_in_memory(self, key: str, value: object) -> bool:
        """Set a metadata key without scheduling persistence."""
        if self.extra_data.get(key) == value:
            return False
        self.extra_data[key] = value
        return True

    def update_metadata(self, updates: dict[str, object]) -> bool:
        """Update multiple metadata keys and schedule persistence if any changed."""
        changed = False
        for key, value in updates.items():
            if self.extra_data.get(key) != value:
                self.extra_data[key] = value
                changed = True

        if changed:
            self.mark_dirty()
        return changed

    def delete_metadata(self, key: str) -> bool:
        """Delete a metadata key and schedule persistence if it existed."""
        if key not in self.extra_data:
            return False
        del self.extra_data[key]
        self.mark_dirty()
        return True

    def delete_metadata_in_memory(self, key: str) -> bool:
        """Delete a metadata key without scheduling persistence."""
        if key not in self.extra_data:
            return False
        del self.extra_data[key]
        return True
    
    def _schedule_persist(self):
        """Schedule debounced persistence (non-blocking).

        Only schedules if file_path is set (opt-in behavior).
        Uses debounce window to batch multiple writes.
        """
        if not self._file_path:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running, skip auto-persist
            # (Will be persisted by MemoryManager.save() later)
            return

        # Cancel existing scheduled task (debounce)
        if self._persist_task and not self._persist_task.done():
            self._persist_task.cancel()

        async def _delayed_persist():
            await asyncio.sleep(self._persist_delay)
            self._do_persist()

        self._persist_task = loop.create_task(_delayed_persist())

    def _do_persist(self):
        """Actually write memory to disk using backend's intelligent persistence."""
        if self._file_path:
            try:
                if self._backend:
                    # Backend handles all persistence logic intelligently
                    self._backend.persist(self)
                else:
                    # Fallback to full save
                    self.save(self._file_path)
                logger.debug(f"Memory '{self.name}' auto-persisted to {self._file_path}")
            except Exception as e:
                logger.error(f"Failed to auto-persist memory '{self.name}': {e}")

    async def flush(self):
        """Force immediate persistence, cancel any pending debounce.
        
        Use this for graceful shutdown to ensure all data is saved.
        """
        if self._persist_task and not self._persist_task.done():
            self._persist_task.cancel()
            try:
                await self._persist_task
            except asyncio.CancelledError:
                pass
        self._do_persist()

    def get_messages(self, execution_context_id=_ALL_CONTEXTS, for_llm: bool = True) -> list[dict]:
        """
        Get the messages from the memory.

        Args:
            execution_context_id: Filter by execution context ID.
            for_llm: If True, applies processing for LLM consumption:
                     - Excludes internal system messages
                     - Handles compression truncation
                     - Normalizes compression messages to user role

        Returns:
            The messages from the memory.
        """
        if execution_context_id is _ALL_CONTEXTS:
            messages = list(self._messages)
        elif execution_context_id is None:
            messages = [
                msg
                for msg in self._messages
                if msg.get("execution_context_id") is None
            ]
        else:
            messages = [
                msg
                for msg in self._messages
                if msg.get("execution_context_id") == execution_context_id
            ]

        if not for_llm:
            return messages

        # --- LLM Processing Logic ---
        from copy import deepcopy

        # 1. Skip system messages (managed externally/prepended)
        filtered = [m for m in messages if m.get("role") != "system"]

        # 2. Find last compression message for truncation
        last_compression_idx = -1
        for i, msg in enumerate(filtered):
            if msg.get("role") == "compression":
                last_compression_idx = i

        # 3. Apply truncation
        if last_compression_idx >= 0:
            filtered = filtered[last_compression_idx:]

        # 4. Final processing (convert compression to user, cleanup metadata)
        final_messages = []
        for msg in filtered:
            msg_copy = deepcopy(msg)

            # Metadata is preserved here for cost tracking. 
            # It will be removed by call_llm_provider before sending to API.
            # if "_metadata" in msg_copy:
            #    del msg_copy["_metadata"]

            if msg_copy.get("role") == "compression":
                msg_copy["role"] = "user"
                # Ensure content is string
                if not isinstance(msg_copy.get("content"), str):
                    msg_copy["content"] = str(msg_copy.get("content", ""))

            final_messages.append(msg_copy)

        return final_messages

    def get_user_turns(self) -> list[tuple[int, dict]]:
        """
        Get all user turns from the memory.

        Returns:
            A list of tuples containing the index and the message.
        """
        return [
            (i, msg)
            for i, msg in enumerate(self._messages)
            if msg.get("role") == "user"
        ]

    def revert_to_message(self, index: int):
        """
        Revert the memory to a specific message index.
        The message at the given index will be REMOVED, along with all subsequent messages.
        Effectively rollback to the state BEFORE that message.

        Args:
            index: The index of the message to revert to (inclusive).
        """
        if 0 <= index < len(self._messages):
            self._messages = self._messages[:index]
            # Force immediate save for revert to prevent race conditions or confusion
            if self._backend:
                self._backend.persist(self)
            elif self._file_path:
                self.save(self._file_path)
        else:
            raise ValueError(f"Invalid message index: {index}")

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

    def _fix_corrupted_messages(self):
        """Remove corrupted messages (missing role and useless).

        Removes messages that only contain partial data (e.g. {'agent_name': ...})
        resulting from failed model calls.
        """
        original_len = len(self._messages)
        self._messages = [
            msg for msg in self._messages
            if msg.get("role") is not None
        ]
        removed_count = original_len - len(self._messages)
        if removed_count > 0:
            logger.warning(
                f"Removed {removed_count} corrupted messages (missing role) from memory '{self.name}'"
            )

    def _fix_orphaned_tool_calls(self):
        """Repair tool-call / tool-result pairing in persisted memory."""
        from pantheon.utils.tool_pairing import ensure_tool_result_pairing_with_stats

        repaired_messages, stats = ensure_tool_result_pairing_with_stats(self._messages)
        if repaired_messages == self._messages:
            return

        self._messages = repaired_messages
        logger.info(
            "Fixed memory '{}': {} placeholder tool response(s) inserted, {} orphan tool message(s) dropped, {} duplicate tool_call(s) dropped, {} duplicate tool response(s) dropped",
            self.name,
            stats.inserted_placeholder_tool_messages,
            stats.dropped_orphan_tool_messages,
            stats.dropped_duplicate_tool_calls,
            stats.dropped_duplicate_tool_messages,
        )
        if stats.dropped_empty_assistant_messages:
            logger.info(
                "Fixed memory '{}': {} empty assistant message(s) dropped after pairing cleanup",
                self.name,
                stats.dropped_empty_assistant_messages,
            )
        self._schedule_persist()

    def ensure_fixed(self):
        """Ensure memory is fixed (idempotent).

        This method can be called multiple times safely. It will only fix
        corrupted messages and orphaned tool calls once, then persist the
        result to disk.

        Use this method when you need to ensure the memory is in a valid
        state for agent execution or LLM API calls.
        """
        if not getattr(self, '_orphans_fixed', False):
            self._fix_corrupted_messages()
            self._fix_orphaned_tool_calls()
            self._orphans_fixed = True
            # Persist the fixed state immediately
            self._schedule_persist()


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

    def __init__(self, path: str | Path, use_jsonl: bool = True):
        self.path = Path(path)
        self.memory_store: dict[str, Memory] = {}
        self.use_jsonl = use_jsonl  # Default format for new memories
        # Don't call self.load() - use on-demand loading instead

    def _load_single_memory(self, memory_id: str) -> Memory:
        """
        Load a single memory from disk (internal helper).

        Args:
            memory_id: The ID of the memory to load.

        Returns:
            Loaded Memory object.

        Raises:
            FileNotFoundError: If the memory file does not exist.
            Exception: If loading fails.
        """
        pseudo_path = self.path / f"{memory_id}.json"
        memory = Memory.load(str(pseudo_path), use_jsonl=self.use_jsonl)
        memory._file_path = str(pseudo_path)

        # Reset running status
        if memory.extra_data.get("running"):
            memory.set_metadata_in_memory("running", False)

        return memory

    def new_memory(self, name: str | None = None) -> Memory:
        """
        Create a new memory.

        Args:
            name: The name of the memory.
        """
        if name is None:
            name = DEFAULT_CHAT_NAME

        # Create memory with configured format
        memory = Memory(name, use_jsonl=self.use_jsonl)
        self.memory_store[memory.id] = memory

        # Enable auto-persistence for managed memories
        memory._file_path = str(self.path / f"{memory.id}.json")

        # Initialize backend based on format
        if self.use_jsonl:
            memory._backend = JSONLBackend(self.path)
            # Create initial metadata file
            memory._backend.save_metadata(memory.id, memory.name, memory.extra_data)
            # Create empty JSONL file
            jsonl_path = self.path / f"{memory.id}.jsonl"
            jsonl_path.touch()
        else:
            memory._backend = JSONBackend(self.path)

        return memory

    def get_memory(self, id: str, auto_fix: bool = False) -> Memory:
        """
        Get a memory by its ID (with on-demand loading).

        Args:
            id: The ID of the memory.
            auto_fix: If True, automatically fix corrupted messages and orphaned
                     tool calls. This is required for agent execution and LLM API
                     calls. If False (default), return the memory as-is for
                     read-only operations like frontend queries.

        Raises:
            KeyError: If the memory with the given ID does not exist.

        Performance Note:
            Setting auto_fix=False skips the fix operations, which can save
            5-10ms for read-only queries. The fix will be applied automatically
            when the memory is used for agent execution.
        """
        # Check if already loaded in cache
        if id in self.memory_store:
            memory = self.memory_store[id]
            if auto_fix:
                memory.ensure_fixed()
            return memory

        # On-demand loading: load single memory file
        if not self.path.exists():
            raise KeyError(f"Chat '{id}' not found. Memory directory does not exist.")

        try:
            memory = self._load_single_memory(id)

            # Cache it
            self.memory_store[memory.id] = memory

            # Apply fix if requested
            if auto_fix:
                memory.ensure_fixed()

            logger.debug(f"Loaded memory on-demand: {memory.name} (id={id})")
            return memory

        except FileNotFoundError:
            raise KeyError(f"Chat '{id}' not found. It may have been deleted.")
        except Exception as e:
            logger.error(f"Failed to load memory {id}: {e}")
            raise KeyError(f"Failed to load chat '{id}': {e}")

    def delete_memory(self, id: str):
        """
        Delete a memory by its ID.

        Args:
            id: The ID of the memory.
        """
        memory = self.memory_store.get(id)

        # Delete from memory store
        del self.memory_store[id]

        # Delete files using backend if available
        if memory and memory._backend:
            memory._backend.delete(id)
        else:
            # Fallback: try to delete both formats
            json_file = self.path / f"{id}.json"
            meta_file = self.path / f"{id}.meta.json"
            jsonl_file = self.path / f"{id}.jsonl"

            if json_file.exists():
                json_file.unlink()
                logger.debug(f"Deleted memory file: {json_file}")
            if meta_file.exists():
                meta_file.unlink()
                logger.debug(f"Deleted memory file: {meta_file}")
            if jsonl_file.exists():
                jsonl_file.unlink()
                logger.debug(f"Deleted memory file: {jsonl_file}")

    def list_memories(self):
        """
        List all the memories (scans directory).

        Returns:
            The list of memory IDs.
        """
        if not self.path.exists():
            return []

        memory_ids = set()

        # Scan JSONL format (.meta.json files)
        for meta_file in self.path.glob("*.meta.json"):
            memory_id = meta_file.stem.replace(".meta", "")
            memory_ids.add(memory_id)

        # Scan JSON format (.json files, excluding .meta.json)
        for json_file in self.path.glob("*.json"):
            if not json_file.name.endswith(".meta.json"):
                memory_id = json_file.stem
                memory_ids.add(memory_id)

        return list(memory_ids)

    def load_all(self):
        """
        Load all memories from disk into memory_store.

        This is useful for testing, migration scenarios, or when you need
        to ensure all memories are loaded upfront instead of on-demand.

        Note: In production, prefer on-demand loading via get_memory() for
        better performance with large memory directories.
        """
        for memory_id in self.list_memories():
            if memory_id not in self.memory_store:
                try:
                    self.get_memory(memory_id)
                except Exception as e:
                    logger.warning(f"Failed to load memory {memory_id}: {e}")

    def save_one(self, memory_id: str):
        """
        Save a single memory to the file system.

        Args:
            memory_id: The ID of the memory to save.
        """
        memory = self.memory_store.get(memory_id)
        if memory:
            # Use backend if available (no file_path argument)
            memory.save()
        else:
            logger.warning(f"Memory {memory_id} not found in memory store, cannot save")

    def save(self):
        """
        Save all the memories to the file system.
        """
        for memory in self.memory_store.values():
            # Use backend if available (no file_path argument)
            memory.save()

        # Clean up orphaned files (both formats)
        existing_ids = set(self.memory_store.keys())

        # Clean up JSON files
        for file in self.path.glob("*.json"):
            if file.stem not in existing_ids and not file.name.endswith(".meta.json"):
                file.unlink()
                logger.debug(f"Cleaned up orphaned file: {file}")

        # Clean up JSONL format files
        for file in self.path.glob("*.meta.json"):
            memory_id = file.stem.replace(".meta", "")
            if memory_id not in existing_ids:
                file.unlink()
                logger.debug(f"Cleaned up orphaned file: {file}")
                # Also clean up corresponding .jsonl file
                jsonl_file = self.path / f"{memory_id}.jsonl"
                if jsonl_file.exists():
                    jsonl_file.unlink()
                    logger.debug(f"Cleaned up orphaned file: {jsonl_file}")

    def load(self):
        """
        Load all the memories from the file system (auto-detects format).
        """
        if not self.path.exists():
            self.path.mkdir(parents=True)

        # Track loaded memory IDs to avoid duplicates
        loaded_ids = set()

        # Load JSONL format memories first (check .meta.json files)
        for meta_file in self.path.glob("*.meta.json"):
            memory_id = meta_file.stem.replace(".meta", "")
            if memory_id in loaded_ids:
                continue

            try:
                memory = self._load_single_memory(memory_id)
                logger.debug(f"Loaded memory (JSONL): {memory.name} from {meta_file}")
                self.memory_store[memory.id] = memory
                loaded_ids.add(memory.id)
            except Exception as e:
                logger.error(f"Failed to load memory from {meta_file}: {e}")

        # Load JSON format memories (only if not already loaded)
        for json_file in self.path.glob("*.json"):
            # Skip .meta.json files
            if json_file.name.endswith(".meta.json"):
                continue

            memory_id = json_file.stem
            if memory_id in loaded_ids:
                continue

            try:
                memory = self._load_single_memory(memory_id)
                logger.debug(f"Loaded memory (JSON): {memory.name} from {json_file}")
                self.memory_store[memory.id] = memory
                loaded_ids.add(memory.id)
            except Exception as e:
                logger.error(f"Failed to load memory from {json_file}: {e}")

    def update_memory_name(self, memory_id: str, name: str):
        """
        Update the name of a memory.

        Args:
            memory_id: The ID of the memory.
            name: The new name of the memory.
        """
        # Read-only: updating memory name, no need to fix
        memory = self.get_memory(memory_id)
        memory.name = name
        self.save_one(memory_id)
