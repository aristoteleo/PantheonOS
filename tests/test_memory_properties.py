"""
Test Memory file_path and storage_files properties.
"""

import tempfile
import shutil
from pathlib import Path

import pytest

from pantheon.internal.memory import Memory, MemoryManager
from pantheon.internal.memory import JSONBackend, JSONLBackend


class TestMemoryPathProperties:
    """Test suite for Memory.file_path and Memory.storage_files properties."""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_file_path_json_backend(self, temp_dir):
        """Test file_path property with JSON backend."""
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path), use_jsonl=False)

        # Initialize backend
        memory._backend = JSONBackend(temp_dir)
        memory._backend.save_metadata(memory.id, memory.name, memory.extra_data)

        # Test file_path property
        assert memory.file_path == str(temp_dir / f"{memory.id}.json")

    def test_file_path_jsonl_backend(self, temp_dir):
        """Test file_path property with JSONL backend."""
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path), use_jsonl=True)

        # Initialize backend
        memory._backend = JSONLBackend(temp_dir)
        memory._backend.save_metadata(memory.id, memory.name, memory.extra_data)

        # Test file_path property (should return .jsonl file)
        assert memory.file_path == str(temp_dir / f"{memory.id}.jsonl")

    def test_file_path_no_backend(self, temp_dir):
        """Test file_path property with no backend."""
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path))
        memory._backend = None

        # Test file_path property (should return _file_path)
        assert memory.file_path == str(file_path)

    def test_storage_files_json_backend(self, temp_dir):
        """Test storage_files property with JSON backend."""
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path), use_jsonl=False)

        # Initialize backend
        memory._backend = JSONBackend(temp_dir)
        memory._backend.save_metadata(memory.id, memory.name, memory.extra_data)

        # Test storage_files property
        files = memory.storage_files
        assert len(files) == 1
        assert files[0] == str(temp_dir / f"{memory.id}.json")

    def test_storage_files_jsonl_backend(self, temp_dir):
        """Test storage_files property with JSONL backend."""
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path), use_jsonl=True)

        # Initialize backend
        memory._backend = JSONLBackend(temp_dir)
        memory._backend.save_metadata(memory.id, memory.name, memory.extra_data)

        # Test storage_files property
        files = memory.storage_files
        assert len(files) == 2
        assert str(temp_dir / f"{memory.id}.meta.json") in files
        assert str(temp_dir / f"{memory.id}.jsonl") in files

    def test_storage_files_no_backend(self, temp_dir):
        """Test storage_files property with no backend."""
        file_path = temp_dir / "test-memory.json"
        memory = Memory("Test Memory", file_path=str(file_path))
        memory._backend = None

        # Test storage_files property
        files = memory.storage_files
        assert len(files) == 1
        assert files[0] == str(file_path)

    def test_storage_files_no_backend_no_path(self):
        """Test storage_files property with no backend and no path."""
        memory = Memory("Test Memory")
        memory._backend = None
        memory._file_path = None

        # Test storage_files property
        files = memory.storage_files
        assert len(files) == 0

    def test_file_path_with_memory_manager_json(self, temp_dir):
        """Test file_path property with MemoryManager (JSON format)."""
        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.new_memory("Test Memory")

        # Test file_path property
        expected_path = str(temp_dir / f"{memory.id}.json")
        assert memory.file_path == expected_path

    def test_file_path_with_memory_manager_jsonl(self, temp_dir):
        """Test file_path property with MemoryManager (JSONL format)."""
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Memory")

        # Test file_path property (should return .jsonl file)
        expected_path = str(temp_dir / f"{memory.id}.jsonl")
        assert memory.file_path == expected_path

    def test_storage_files_with_memory_manager_json(self, temp_dir):
        """Test storage_files property with MemoryManager (JSON format)."""
        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.new_memory("Test Memory")

        # Test storage_files property
        files = memory.storage_files
        assert len(files) == 1
        assert files[0] == str(temp_dir / f"{memory.id}.json")

    def test_storage_files_with_memory_manager_jsonl(self, temp_dir):
        """Test storage_files property with MemoryManager (JSONL format)."""
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Memory")

        # Test storage_files property
        files = memory.storage_files
        assert len(files) == 2
        assert str(temp_dir / f"{memory.id}.meta.json") in files
        assert str(temp_dir / f"{memory.id}.jsonl") in files

    def test_file_path_after_load_json(self, temp_dir):
        """Test file_path property after loading JSON format."""
        # Create and save a memory
        manager = MemoryManager(temp_dir, use_jsonl=False)
        memory = manager.new_memory("Test Memory")
        memory.add_messages([{"role": "user", "content": "Hello"}])
        manager.save()

        # Load and test
        manager2 = MemoryManager(temp_dir, use_jsonl=False)
        loaded_memory = manager2.get_memory(memory.id)

        assert loaded_memory.file_path == str(temp_dir / f"{memory.id}.json")

    def test_file_path_after_load_jsonl(self, temp_dir):
        """Test file_path property after loading JSONL format."""
        # Create and save a memory
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Memory")
        memory.add_messages([{"role": "user", "content": "Hello"}])
        manager.save()

        # Load and test
        manager2 = MemoryManager(temp_dir, use_jsonl=True)
        loaded_memory = manager2.get_memory(memory.id)

        assert loaded_memory.file_path == str(temp_dir / f"{memory.id}.jsonl")

    def test_storage_files_after_load_jsonl(self, temp_dir):
        """Test storage_files property after loading JSONL format."""
        # Create and save a memory
        manager = MemoryManager(temp_dir, use_jsonl=True)
        memory = manager.new_memory("Test Memory")
        memory.add_messages([{"role": "user", "content": "Hello"}])
        manager.save()

        # Load and test
        manager2 = MemoryManager(temp_dir, use_jsonl=True)
        loaded_memory = manager2.get_memory(memory.id)

        files = loaded_memory.storage_files
        assert len(files) == 2
        assert str(temp_dir / f"{memory.id}.meta.json") in files
        assert str(temp_dir / f"{memory.id}.jsonl") in files

        # Verify files actually exist
        for f in files:
            assert Path(f).exists()


class TestMemoryMetadataHelpers:
    def test_set_metadata_marks_dirty_only_on_change(self):
        memory = Memory("Test Memory")
        calls = []
        memory.mark_dirty = lambda: calls.append("dirty")  # type: ignore[method-assign]

        assert memory.set_metadata("running", True) is True
        assert memory.extra_data["running"] is True
        assert calls == ["dirty"]

        assert memory.set_metadata("running", True) is False
        assert calls == ["dirty"]

    def test_update_metadata_marks_dirty_once(self):
        memory = Memory("Test Memory")
        calls = []
        memory.mark_dirty = lambda: calls.append("dirty")  # type: ignore[method-assign]

        assert memory.update_metadata({"running": True, "active_agent": "Leader"}) is True
        assert memory.extra_data["running"] is True
        assert memory.extra_data["active_agent"] == "Leader"
        assert calls == ["dirty"]

        assert memory.update_metadata({"running": True, "active_agent": "Leader"}) is False
        assert calls == ["dirty"]

    def test_set_metadata_in_memory_does_not_mark_dirty(self):
        memory = Memory("Test Memory")
        calls = []
        memory.mark_dirty = lambda: calls.append("dirty")  # type: ignore[method-assign]

        assert memory.set_metadata_in_memory("running", True) is True
        assert memory.extra_data["running"] is True
        assert calls == []

        assert memory.set_metadata_in_memory("running", True) is False
        assert calls == []

    def test_delete_metadata_marks_dirty_only_when_key_exists(self):
        memory = Memory("Test Memory")
        memory.extra_data["running"] = True
        calls = []
        memory.mark_dirty = lambda: calls.append("dirty")  # type: ignore[method-assign]

        assert memory.delete_metadata("running") is True
        assert "running" not in memory.extra_data
        assert calls == ["dirty"]

        assert memory.delete_metadata("running") is False
        assert calls == ["dirty"]

    def test_delete_metadata_in_memory_does_not_mark_dirty(self):
        memory = Memory("Test Memory")
        memory.extra_data["running"] = True
        calls = []
        memory.mark_dirty = lambda: calls.append("dirty")  # type: ignore[method-assign]

        assert memory.delete_metadata_in_memory("running") is True
        assert "running" not in memory.extra_data
        assert calls == []

        assert memory.delete_metadata_in_memory("running") is False
        assert calls == []
