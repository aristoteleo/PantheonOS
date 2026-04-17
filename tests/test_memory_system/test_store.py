"""Tests for MemoryStore file operations."""

import time
from datetime import datetime, timezone

import pytest

from pantheon.internal.memory_system.store import MemoryStore
from pantheon.internal.memory_system.types import MemoryEntry, MemoryType


class TestScanHeaders:
    def test_empty_dir(self, store):
        assert store.scan_headers() == []

    def test_scans_memory_files(self, populated_store):
        headers = populated_store.scan_headers()
        assert len(headers) == 3
        types = {h.type for h in headers}
        assert MemoryType.USER in types
        assert MemoryType.FEEDBACK in types
        assert MemoryType.WORKFLOW in types

    def test_excludes_logs_dir(self, store, sample_user_entry):
        store.add_memory(sample_user_entry)
        store.append_daily_log("test log entry")
        headers = store.scan_headers()
        assert len(headers) == 1

    def test_sorted_by_mtime_descending(self, store):
        for i in range(3):
            entry = MemoryEntry(title=f"Memory {i}", summary=f"Desc {i}",
                               type=MemoryType.WORKFLOW, content=f"Content {i}")
            store.add_memory(entry)
            time.sleep(0.05)
        headers = store.scan_headers()
        for i in range(len(headers) - 1):
            assert headers[i].mtime >= headers[i + 1].mtime

    def test_headers_have_title_and_summary(self, populated_store):
        headers = populated_store.scan_headers()
        for h in headers:
            assert h.title
            assert h.summary


class TestIndex:
    def test_read_empty(self, store):
        assert store.read_index() == ""

    def test_write_and_read(self, store):
        store.write_index("- [Test](memory/test.md) — A test memory")
        content = store.read_index()
        assert "Test" in content

    def test_index_at_pantheon_dir(self, store, tmp_pantheon_dir):
        store.write_index("test content")
        assert store.index_path == tmp_pantheon_dir / "MEMORY.md"
        assert store.index_path.exists()

    def test_durable_dir_separate_from_index(self, store, tmp_pantheon_dir):
        assert store.durable_dir == tmp_pantheon_dir / "memory-store"
        assert store.index_path == tmp_pantheon_dir / "MEMORY.md"

    def test_truncation_lines(self, store):
        lines = [f"- [Line {i}](memory/line{i}.md) — Line {i}" for i in range(300)]
        store.write_index("\n".join(lines))
        content = store.read_index()
        assert content.count("\n") < 201

    def test_truncation_bytes(self, store):
        line = "- [" + "x" * 200 + "](test.md) — " + "y" * 200
        lines = [line] * 200
        store.write_index("\n".join(lines))
        content = store.read_index()
        assert len(content.encode("utf-8")) <= MemoryStore.MAX_INDEX_BYTES


class TestCRUD:
    def test_add_memory(self, store, sample_user_entry):
        path = store.add_memory(sample_user_entry)
        assert path.exists()
        assert path.suffix == ".md"
        assert path.parent == store.durable_dir
        # Index should be updated
        index = store.read_index()
        assert sample_user_entry.title in index

    def test_add_avoids_overwrite(self, store, sample_user_entry):
        path1 = store.add_memory(sample_user_entry)
        path2 = store.add_memory(sample_user_entry)
        assert path1 != path2
        assert path1.exists() and path2.exists()

    def test_read_memory(self, store, sample_feedback_entry):
        path = store.add_memory(sample_feedback_entry)
        loaded = store.read_memory(path)
        assert loaded.title == sample_feedback_entry.title
        assert loaded.type == MemoryType.FEEDBACK

    def test_update_memory(self, store, sample_user_entry):
        path = store.add_memory(sample_user_entry)
        updated = MemoryEntry(title="Updated", summary="Updated desc",
                             type=MemoryType.USER, content="Updated content")
        store.update_memory(path, updated)
        loaded = store.read_memory(path)
        assert loaded.title == "Updated"

    def test_delete_memory(self, store, sample_workflow_entry):
        path = store.add_memory(sample_workflow_entry)
        assert path.exists()
        store.delete_memory(path)
        assert not path.exists()
        assert path.name not in store.read_index()

    def test_find_memory_by_name(self, store, sample_user_entry):
        path = store.add_memory(sample_user_entry)
        found = store.find_memory_by_name(path.name)
        assert found == path

    def test_find_memory_not_found(self, store):
        assert store.find_memory_by_name("nonexistent.md") is None


class TestDailyLog:
    def test_append_creates_file(self, store):
        dt = datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc)
        path = store.append_daily_log("Test log entry", date=dt)
        assert path.exists()
        content = path.read_text()
        assert "2026-04-01" in content
        assert "14:30" in content

    def test_append_to_existing(self, store):
        dt1 = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
        store.append_daily_log("First entry", date=dt1)
        dt2 = datetime(2026, 4, 1, 15, 0, tzinfo=timezone.utc)
        path = store.append_daily_log("Second entry", date=dt2)
        content = path.read_text()
        assert "First entry" in content and "Second entry" in content

    def test_list_daily_logs(self, store):
        for day in [1, 2, 3]:
            store.append_daily_log(f"Day {day}", date=datetime(2026, 4, day, tzinfo=timezone.utc))
        assert len(store.list_daily_logs()) == 3

    def test_list_daily_logs_since(self, store):
        for day in [1, 2, 3]:
            store.append_daily_log(f"Day {day}", date=datetime(2026, 4, day, tzinfo=timezone.utc))
        logs = store.list_daily_logs(since=datetime(2026, 4, 2, tzinfo=timezone.utc))
        assert len(logs) == 2

    def test_list_empty(self, store):
        assert store.list_daily_logs() == []
