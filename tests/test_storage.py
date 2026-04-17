"""
Comprehensive tests for storage backends and memory integration.

Tests cover:
- JSONBackend and JSONLBackend implementations
- Memory class integration with backends
- MemoryManager integration with mixed formats
- Format detection and auto-routing
- Performance characteristics
- Backward compatibility
"""

import json
import threading
import tempfile
import shutil
import time
from pathlib import Path

import pytest

from pantheon.internal.memory import Memory, MemoryManager
from pantheon.internal.memory import StorageBackend, JSONBackend, JSONLBackend


# ============================================================================
# PART 1: Storage Backend Tests
# ============================================================================

class TestJSONBackend:
    """Test suite for JSONBackend (legacy format)."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def backend(self, temp_dir):
        return JSONBackend(temp_dir)

    def test_save_and_load_metadata(self, backend):
        memory_id = "test-memory-1"
        name = "Test Memory"
        extra_data = {"key": "value", "count": 42}

        backend.save_metadata(memory_id, name, extra_data)
        loaded_id, loaded_name, loaded_extra = backend.load_metadata(memory_id)

        assert loaded_id == memory_id
        assert loaded_name == name
        assert loaded_extra == extra_data

    def test_append_messages(self, backend):
        memory_id = "test-memory-2"
        backend.save_metadata(memory_id, "Test", {})

        messages1 = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        backend.append_messages(memory_id, messages1)

        messages2 = [{"role": "user", "content": "How are you?"}]
        backend.append_messages(memory_id, messages2)

        loaded_messages = backend.load_messages(memory_id)
        assert len(loaded_messages) == 3
        assert loaded_messages[0]["content"] == "Hello"
        assert loaded_messages[2]["content"] == "How are you?"

    def test_rewrite_messages(self, backend):
        memory_id = "test-memory-4"
        backend.save_metadata(memory_id, "Test", {})
        backend.append_messages(memory_id, [
            {"role": "user", "content": "Old message 1"},
            {"role": "user", "content": "Old message 2"},
        ])

        new_messages = [{"role": "user", "content": "New message"}]
        backend.rewrite_messages(memory_id, new_messages)

        loaded_messages = backend.load_messages(memory_id)
        assert len(loaded_messages) == 1
        assert loaded_messages[0]["content"] == "New message"

    def test_exists_and_delete(self, backend, temp_dir):
        memory_id = "test-memory-5"
        assert not backend.exists(memory_id)

        backend.save_metadata(memory_id, "Test", {})
        assert backend.exists(memory_id)

        backend.delete(memory_id)
        assert not backend.exists(memory_id)

    def test_detect_format(self, temp_dir):
        memory_id = "test-memory-7"
        json_file = temp_dir / f"{memory_id}.json"
        json_file.write_text('{"id": "test"}')

        assert JSONBackend.detect_format(temp_dir, memory_id)
        assert not JSONLBackend.detect_format(temp_dir, memory_id)

    def test_save_metadata_failure_preserves_existing_file(self, backend, temp_dir, monkeypatch):
        memory_id = "test-memory-atomic-json"
        backend.save_metadata(memory_id, "Original Name", {"version": 1})

        original_dump = json.dump

        def broken_dump(obj, fp, *args, **kwargs):
            if obj.get("name") == "Updated Name":
                fp.write('{"id": "broken')
                raise RuntimeError("simulated write failure")
            return original_dump(obj, fp, *args, **kwargs)

        monkeypatch.setattr("pantheon.internal.memory.storage.json.dump", broken_dump)

        with pytest.raises(RuntimeError, match="simulated write failure"):
            backend.save_metadata(memory_id, "Updated Name", {"version": 2})

        loaded_id, loaded_name, loaded_extra = backend.load_metadata(memory_id)
        assert loaded_id == memory_id
        assert loaded_name == "Original Name"
        assert loaded_extra == {"version": 1}


class TestJSONLBackend:
    """Test suite for JSONLBackend (high-performance format)."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def backend(self, temp_dir):
        return JSONLBackend(temp_dir)

    def test_save_and_load_metadata(self, backend):
        memory_id = "test-memory-1"
        name = "Test Memory"
        extra_data = {"key": "value", "count": 42}

        backend.save_metadata(memory_id, name, extra_data)
        loaded_id, loaded_name, loaded_extra = backend.load_metadata(memory_id)

        assert loaded_id == memory_id
        assert loaded_name == name
        assert loaded_extra == extra_data

    def test_append_messages(self, backend):
        memory_id = "test-memory-2"
        backend.save_metadata(memory_id, "Test", {})

        messages1 = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        backend.append_messages(memory_id, messages1)

        messages2 = [{"role": "user", "content": "How are you?"}]
        backend.append_messages(memory_id, messages2)

        loaded_messages = backend.load_messages(memory_id)
        assert len(loaded_messages) == 3
        assert loaded_messages[0]["content"] == "Hello"
        assert loaded_messages[2]["content"] == "How are you?"

    def test_metadata_update_independent(self, backend, temp_dir):
        """Test that metadata updates don't touch messages file."""
        memory_id = "test-memory-8"
        backend.save_metadata(memory_id, "Original Name", {"key": "value1"})
        backend.append_messages(memory_id, [{"role": "user", "content": "Message 1"}])

        jsonl_file = temp_dir / f"{memory_id}.jsonl"
        mtime_before = jsonl_file.stat().st_mtime

        import time
        time.sleep(0.01)
        backend.save_metadata(memory_id, "Updated Name", {"key": "value2"})

        mtime_after = jsonl_file.stat().st_mtime
        assert mtime_before == mtime_after

    def test_jsonl_format_one_per_line(self, backend, temp_dir):
        memory_id = "test-memory-9"
        backend.save_metadata(memory_id, "Test", {})
        backend.append_messages(memory_id, [
            {"role": "user", "content": "Line 1"},
            {"role": "assistant", "content": "Line 2"},
        ])

        jsonl_file = temp_dir / f"{memory_id}.jsonl"
        lines = jsonl_file.read_text().strip().split("\n")

        assert len(lines) == 2
        msg1 = json.loads(lines[0])
        msg2 = json.loads(lines[1])
        assert msg1["content"] == "Line 1"
        assert msg2["content"] == "Line 2"

    def test_detect_format(self, temp_dir):
        memory_id = "test-memory-7"
        meta_file = temp_dir / f"{memory_id}.meta.json"
        meta_file.write_text('{"id": "test"}')

        assert JSONLBackend.detect_format(temp_dir, memory_id)
        assert not JSONBackend.detect_format(temp_dir, memory_id)

    def test_save_metadata_failure_preserves_existing_file(self, backend, temp_dir, monkeypatch):
        memory_id = "test-memory-atomic"
        backend.save_metadata(memory_id, "Original Name", {"version": 1})

        original_dump = json.dump

        def broken_dump(obj, fp, *args, **kwargs):
            if obj.get("name") == "Updated Name":
                fp.write('{"id": "broken')
                raise RuntimeError("simulated write failure")
            return original_dump(obj, fp, *args, **kwargs)

        monkeypatch.setattr("pantheon.internal.memory.storage.json.dump", broken_dump)

        with pytest.raises(RuntimeError, match="simulated write failure"):
            backend.save_metadata(memory_id, "Updated Name", {"version": 2})

        loaded_id, loaded_name, loaded_extra = backend.load_metadata(memory_id)
        assert loaded_id == memory_id
        assert loaded_name == "Original Name"
        assert loaded_extra == {"version": 1}


class TestFormatDetection:
    """Test format detection logic."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_jsonl_takes_precedence(self, temp_dir):
        """Test that JSONL format takes precedence if both exist."""
        memory_id = "both-formats"
        json_file = temp_dir / f"{memory_id}.json"
        meta_file = temp_dir / f"{memory_id}.meta.json"
        json_file.write_text('{"id": "test"}')
        meta_file.write_text('{"id": "test"}')

        assert JSONLBackend.detect_format(temp_dir, memory_id)
        assert not JSONBackend.detect_format(temp_dir, memory_id)

    def test_no_format_detected(self, temp_dir):
        memory_id = "nonexistent"
        assert not JSONBackend.detect_format(temp_dir, memory_id)
        assert not JSONLBackend.detect_format(temp_dir, memory_id)


class TestPerformance:
    """Test performance characteristics of backends."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_jsonl_append_is_faster(self, temp_dir):
        """Test that JSONL append is significantly faster than JSON."""
        import time

        json_backend = JSONBackend(temp_dir)
        json_id = "json-perf"
        json_backend.save_metadata(json_id, "Test", {})

        start = time.time()
        for i in range(50):
            json_backend.append_messages(json_id, [{"role": "user", "content": f"Msg {i}"}])
        json_time = time.time() - start

        jsonl_backend = JSONLBackend(temp_dir)
        jsonl_id = "jsonl-perf"
        jsonl_backend.save_metadata(jsonl_id, "Test", {})

        start = time.time()
        for i in range(50):
            jsonl_backend.append_messages(jsonl_id, [{"role": "user", "content": f"Msg {i}"}])
        jsonl_time = time.time() - start

        speedup = json_time / jsonl_time
        print(f"\nPerformance: JSON={json_time:.3f}s, JSONL={jsonl_time:.3f}s, Speedup={speedup:.1f}x")
        assert speedup > 5, f"Expected JSONL to be >5x faster, got {speedup:.1f}x"


class TestMemoryLocks:
    """Test per-memory write lock behavior."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def _assert_serialized_writes(self, backend, monkeypatch):
        memory_id = "locked-memory"
        original_atomic_write = backend._atomic_write_json
        state = {"active": 0, "max_active": 0}
        state_lock = threading.Lock()

        def instrumented_atomic_write(path, data):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                time.sleep(0.05)
                return original_atomic_write(path, data)
            finally:
                with state_lock:
                    state["active"] -= 1

        monkeypatch.setattr(backend, "_atomic_write_json", instrumented_atomic_write)

        threads = [
            threading.Thread(
                target=backend.save_metadata,
                args=(memory_id, f"Name {i}", {"version": i}),
            )
            for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert state["max_active"] == 1

    def test_json_backend_serializes_same_memory_writes(self, temp_dir, monkeypatch):
        backend = JSONBackend(temp_dir)
        self._assert_serialized_writes(backend, monkeypatch)

    def test_jsonl_backend_serializes_same_memory_writes(self, temp_dir, monkeypatch):
        backend = JSONLBackend(temp_dir)
        self._assert_serialized_writes(backend, monkeypatch)


class TestRealMemorySamples:
    """Compatibility tests using real memory files from .pantheon/memory."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def real_memory_dir(self):
        memory_dir = Path(__file__).resolve().parents[1] / ".pantheon" / "memory"
        assert memory_dir.exists(), f"Real memory dir not found: {memory_dir}"
        return memory_dir

    def _copy_real_json_sample(self, source_dir: Path, dest_dir: Path) -> str:
        for json_file in sorted(source_dir.glob("*.json")):
            if json_file.name.endswith(".meta.json"):
                continue
            try:
                data = json.loads(json_file.read_text("utf-8"))
            except Exception:
                continue
            if {"id", "name", "messages"} <= set(data.keys()):
                shutil.copy2(json_file, dest_dir / json_file.name)
                return json_file.stem
        raise AssertionError("No valid real JSON memory sample found")

    def _copy_real_jsonl_sample(self, source_dir: Path, dest_dir: Path) -> str:
        for meta_file in sorted(source_dir.glob("*.meta.json")):
            memory_id = meta_file.name[:-10]
            jsonl_file = source_dir / f"{memory_id}.jsonl"
            if not jsonl_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text("utf-8"))
                lines = [line for line in jsonl_file.read_text("utf-8").splitlines() if line.strip()]
                for line in lines:
                    json.loads(line)
            except Exception:
                continue
            if {"id", "name", "extra_data"} <= set(meta.keys()):
                shutil.copy2(meta_file, dest_dir / meta_file.name)
                shutil.copy2(jsonl_file, dest_dir / jsonl_file.name)
                return memory_id
        raise AssertionError("No valid real JSONL memory sample found")

    def test_real_json_sample_round_trip(self, temp_dir, real_memory_dir):
        memory_id = self._copy_real_json_sample(real_memory_dir, temp_dir)

        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.get_memory(memory_id)
        original_message_count = len(memory._messages)

        assert isinstance(memory._backend, JSONBackend)
        assert original_message_count >= 1

        memory.set_metadata("round_trip_marker", "json-backend")
        manager.save_one(memory_id)

        manager2 = MemoryManager(temp_dir, use_jsonl=False)
        reloaded = manager2.get_memory(memory_id)

        assert isinstance(reloaded._backend, JSONBackend)
        assert len(reloaded._messages) == original_message_count
        assert reloaded.extra_data["round_trip_marker"] == "json-backend"

    def test_real_jsonl_sample_round_trip(self, temp_dir, real_memory_dir):
        memory_id = self._copy_real_jsonl_sample(real_memory_dir, temp_dir)

        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.get_memory(memory_id)
        original_message_count = len(memory._messages)

        assert isinstance(memory._backend, JSONLBackend)

        memory.set_metadata("round_trip_marker", "jsonl-backend")
        manager.save_one(memory_id)

        manager2 = MemoryManager(temp_dir, use_jsonl=True)
        reloaded = manager2.get_memory(memory_id)

        assert isinstance(reloaded._backend, JSONLBackend)
        assert len(reloaded._messages) == original_message_count
        assert reloaded.extra_data["round_trip_marker"] == "jsonl-backend"


# ============================================================================
# PART 2: Memory Integration Tests
# ============================================================================

class TestMemoryBackendIntegration:
    """Test Memory class integration with storage backends."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_new_memory_uses_jsonl_by_default(self, temp_dir):
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path), use_jsonl=True)
        assert isinstance(memory._backend, JSONLBackend)

    def test_load_detects_json_format(self, temp_dir):
        memory_id = "json-memory"
        json_file = temp_dir / f"{memory_id}.json"

        with open(json_file, "w") as f:
            json.dump({
                "id": memory_id,
                "name": "JSON Memory",
                "messages": [{"role": "user", "content": "Hello"}],
                "extra_data": {"key": "value"}
            }, f)

        memory = Memory.load(str(json_file))
        assert isinstance(memory._backend, JSONBackend)
        assert memory.name == "JSON Memory"
        assert len(memory._messages) == 1

    def test_load_detects_jsonl_format(self, temp_dir):
        memory_id = "jsonl-memory"
        meta_file = temp_dir / f"{memory_id}.meta.json"
        jsonl_file = temp_dir / f"{memory_id}.jsonl"

        with open(meta_file, "w") as f:
            json.dump({
                "id": memory_id,
                "name": "JSONL Memory",
                "extra_data": {"key": "value"}
            }, f)

        with open(jsonl_file, "w") as f:
            f.write(json.dumps({"role": "user", "content": "Hello"}) + "\n")

        pseudo_path = temp_dir / f"{memory_id}.json"
        memory = Memory.load(str(pseudo_path))
        assert isinstance(memory._backend, JSONLBackend)
        assert memory.name == "JSONL Memory"

    def test_add_messages_uses_backend_append(self, temp_dir):
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path), use_jsonl=True)

        memory._backend.save_metadata(memory.id, memory.name, memory.extra_data)
        jsonl_file = temp_dir / f"{memory.id}.jsonl"
        jsonl_file.touch()

        memory.add_messages([
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Reply 1"},
        ])

        assert len(memory._messages) == 2

        # Force immediate persistence (add_messages now uses debounced persistence)
        memory._do_persist()

        with open(jsonl_file, "r") as f:
            lines = f.readlines()
        assert len(lines) == 2

    def test_revert_to_message_uses_backend(self, temp_dir):
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path), use_jsonl=True)

        memory._backend.save_metadata(memory.id, memory.name, memory.extra_data)
        jsonl_file = temp_dir / f"{memory.id}.jsonl"
        jsonl_file.touch()

        memory.add_messages([
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Reply 1"},
            {"role": "user", "content": "Message 2"},
            {"role": "assistant", "content": "Reply 2"},
        ])

        memory.revert_to_message(2)

        assert len(memory._messages) == 2
        with open(jsonl_file, "r") as f:
            lines = f.readlines()
        assert len(lines) == 2

    def test_backward_compatibility_no_backend(self, temp_dir):
        memory = Memory("Test Memory")
        assert memory._backend is None

        memory.add_messages([{"role": "user", "content": "Test"}])
        assert len(memory._messages) == 1

        save_path = temp_dir / "legacy.json"
        memory.save(str(save_path))

        with open(save_path, "r") as f:
            data = json.load(f)
        assert data["name"] == "Test Memory"
        assert len(data["messages"]) == 1


# ============================================================================
# PART 3: MemoryManager Integration Tests
# ============================================================================

class TestMemoryManagerBasics:
    """Test basic MemoryManager functionality with backends."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_new_memory_creates_jsonl_format(self, temp_dir):
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Chat")

        assert isinstance(memory._backend, JSONLBackend)

        meta_file = temp_dir / f"{memory.id}.meta.json"
        jsonl_file = temp_dir / f"{memory.id}.jsonl"
        assert meta_file.exists()
        assert jsonl_file.exists()

    def test_new_memory_creates_json_format(self, temp_dir):
        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.new_memory("Test Chat")
        assert isinstance(memory._backend, JSONBackend)

    def test_get_memory(self, temp_dir):
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Chat")

        retrieved = manager.get_memory(memory.id)
        assert retrieved.id == memory.id
        assert retrieved.name == "Test Chat"

    def test_list_memories(self, temp_dir):
        manager = MemoryManager(temp_dir, use_jsonl=True)
        mem1 = manager.new_memory("Chat 1")
        mem2 = manager.new_memory("Chat 2")

        memory_ids = manager.list_memories()
        assert len(memory_ids) == 2
        assert mem1.id in memory_ids
        assert mem2.id in memory_ids


class TestMemoryManagerLoad:
    """Test MemoryManager load functionality with mixed formats."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_load_mixed_formats(self, temp_dir):
        """Test loading both JSON and JSONL formats simultaneously."""
        json_id = "json-memory"
        json_file = temp_dir / f"{json_id}.json"
        with open(json_file, "w") as f:
            json.dump({
                "id": json_id,
                "name": "JSON Memory",
                "messages": [],
                "extra_data": {}
            }, f)

        jsonl_id = "jsonl-memory"
        meta_file = temp_dir / f"{jsonl_id}.meta.json"
        jsonl_file = temp_dir / f"{jsonl_id}.jsonl"
        with open(meta_file, "w") as f:
            json.dump({
                "id": jsonl_id,
                "name": "JSONL Memory",
                "extra_data": {}
            }, f)
        jsonl_file.touch()

        manager = MemoryManager(temp_dir, use_jsonl=True)
        manager.load_all()

        assert len(manager.memory_store) == 2
        assert json_id in manager.memory_store
        assert jsonl_id in manager.memory_store

        json_memory = manager.get_memory(json_id)
        jsonl_memory = manager.get_memory(jsonl_id)

        assert isinstance(json_memory._backend, JSONBackend)
        assert isinstance(jsonl_memory._backend, JSONLBackend)


class TestMemoryManagerDelete:
    """Test MemoryManager delete functionality with both formats."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_delete_jsonl_memory(self, temp_dir):
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Chat")
        memory.add_messages([{"role": "user", "content": "Test"}])

        meta_file = temp_dir / f"{memory.id}.meta.json"
        jsonl_file = temp_dir / f"{memory.id}.jsonl"
        assert meta_file.exists()
        assert jsonl_file.exists()

        manager.delete_memory(memory.id)

        assert not meta_file.exists()
        assert not jsonl_file.exists()
        assert memory.id not in manager.memory_store


class TestMemoryManagerWorkflow:
    """Test real-world workflows with mixed formats."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_gradual_migration_workflow(self, temp_dir):
        """Test gradual migration: old chats stay JSON, new chats use JSONL."""
        manager1 = MemoryManager(temp_dir, use_jsonl=False)
        old_memory = manager1.new_memory("Old Chat")
        old_memory.add_messages([{"role": "user", "content": "Old message"}])
        old_memory.save(str(temp_dir / f"{old_memory.id}.json"))

        manager2 = MemoryManager(temp_dir, use_jsonl=True)

        old_loaded = manager2.get_memory(old_memory.id)
        assert isinstance(old_loaded._backend, JSONBackend)

        new_memory = manager2.new_memory("New Chat")
        assert isinstance(new_memory._backend, JSONLBackend)

        old_loaded.add_messages([{"role": "user", "content": "Another old message"}])
        new_memory.add_messages([{"role": "user", "content": "New message"}])

        manager3 = MemoryManager(temp_dir, use_jsonl=True)
        manager3.load_all()
        assert len(manager3.memory_store) == 2


class TestMemoryFixOperations:
    """Test that fix operations work correctly with both backends."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_fix_corrupted_messages_jsonl(self, temp_dir):
        """Test that fixing corrupted messages persists correctly with JSONL backend."""
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Chat")

        # Add normal message
        memory.add_messages([{"role": "user", "content": "Normal message"}])

        # Manually add corrupted message (missing role)
        memory._messages.append({"agent_name": "test", "content": "Corrupted"})

        # Fix should remove corrupted message
        memory.ensure_fixed()

        # Verify in-memory state
        assert len(memory._messages) == 1
        assert memory._messages[0]["content"] == "Normal message"

        # Force persist
        memory._do_persist()

        # Reload and verify persistence
        manager2 = MemoryManager(temp_dir, use_jsonl=True)
        memory2 = manager2.get_memory(memory.id)

        assert len(memory2._messages) == 1
        assert memory2._messages[0]["content"] == "Normal message"

    def test_fix_corrupted_messages_json(self, temp_dir):
        """Test that fixing corrupted messages persists correctly with JSON backend."""
        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.new_memory("Test Chat")

        # Add normal message
        memory.add_messages([{"role": "user", "content": "Normal message"}])

        # Manually add corrupted message (missing role)
        memory._messages.append({"agent_name": "test", "content": "Corrupted"})

        # Fix should remove corrupted message
        memory.ensure_fixed()

        # Verify in-memory state
        assert len(memory._messages) == 1

        # Force persist
        memory._do_persist()

        # Reload and verify persistence
        manager2 = MemoryManager(temp_dir, use_jsonl=False)
        memory2 = manager2.get_memory(memory.id)

        assert len(memory2._messages) == 1
        assert memory2._messages[0]["content"] == "Normal message"

    def test_fix_orphaned_tool_calls_jsonl(self, temp_dir):
        """Test that fixing orphaned tool calls persists correctly with JSONL backend."""
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Chat")

        # Add assistant message with tool call
        memory.add_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {"name": "test_tool", "arguments": "{}"}
            }]
        }])

        # Fix should add placeholder tool response
        memory.ensure_fixed()

        # Verify in-memory state
        assert len(memory._messages) == 2
        assert memory._messages[1]["role"] == "tool"
        assert memory._messages[1]["tool_call_id"] == "call_123"
        assert "[INTERNAL_ERROR]" in memory._messages[1]["content"]

        # Force persist
        memory._do_persist()

        # Reload and verify persistence
        manager2 = MemoryManager(temp_dir, use_jsonl=True)
        memory2 = manager2.get_memory(memory.id)

        assert len(memory2._messages) == 2
        assert memory2._messages[1]["role"] == "tool"
        assert memory2._messages[1]["tool_call_id"] == "call_123"

    def test_fix_orphaned_tool_calls_json(self, temp_dir):
        """Test that fixing orphaned tool calls persists correctly with JSON backend."""
        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.new_memory("Test Chat")

        # Add assistant message with tool call
        memory.add_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_456",
                "type": "function",
                "function": {"name": "test_tool", "arguments": "{}"}
            }]
        }])

        # Fix should add placeholder tool response
        memory.ensure_fixed()

        # Verify in-memory state
        assert len(memory._messages) == 2
        assert memory._messages[1]["role"] == "tool"

        # Force persist
        memory._do_persist()

        # Reload and verify persistence
        manager2 = MemoryManager(temp_dir, use_jsonl=False)
        memory2 = manager2.get_memory(memory.id)

        assert len(memory2._messages) == 2
        assert memory2._messages[1]["role"] == "tool"
        assert memory2._messages[1]["tool_call_id"] == "call_456"


class TestMemoryExplicitSave:
    """Test explicit save to file path (user-specified locations)."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_save_to_explicit_path_from_jsonl_backend(self, temp_dir):
        """Test that saving to explicit path creates portable JSON file (from JSONL backend)."""
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Chat")
        memory.add_messages([
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Reply 1"},
        ])

        # Save to explicit path (should create portable JSON)
        export_path = temp_dir / "exported_chat.json"
        memory.save(str(export_path))

        # Verify it's a single JSON file
        assert export_path.exists()
        with open(export_path, "r") as f:
            data = json.load(f)

        assert data["name"] == "Test Chat"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["content"] == "Message 1"

        # Verify original JSONL files still exist
        meta_file = temp_dir / f"{memory.id}.meta.json"
        jsonl_file = temp_dir / f"{memory.id}.jsonl"
        assert meta_file.exists()
        assert jsonl_file.exists()

    def test_save_to_explicit_path_from_json_backend(self, temp_dir):
        """Test that saving to explicit path works from JSON backend."""
        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.new_memory("Test Chat")
        memory.add_messages([{"role": "user", "content": "Message 1"}])

        # Save to explicit path
        export_path = temp_dir / "exported_chat.json"
        memory.save(str(export_path))

        # Verify it's a single JSON file
        assert export_path.exists()
        with open(export_path, "r") as f:
            data = json.load(f)

        assert data["name"] == "Test Chat"
        assert len(data["messages"]) == 1

    def test_manager_save_uses_backend(self, temp_dir):
        """Test that MemoryManager.save() uses backend correctly."""
        manager = MemoryManager(temp_dir, use_jsonl=True)
        mem1 = manager.new_memory("Chat 1")
        mem2 = manager.new_memory("Chat 2")

        mem1.add_messages([{"role": "user", "content": "Test 1"}])
        mem2.add_messages([{"role": "user", "content": "Test 2"}])

        # Save all
        manager.save()

        # Verify JSONL files exist
        assert (temp_dir / f"{mem1.id}.meta.json").exists()
        assert (temp_dir / f"{mem1.id}.jsonl").exists()
        assert (temp_dir / f"{mem2.id}.meta.json").exists()
        assert (temp_dir / f"{mem2.id}.jsonl").exists()

        # Reload and verify
        manager2 = MemoryManager(temp_dir, use_jsonl=True)
        manager2.load_all()
        assert len(manager2.memory_store) == 2
