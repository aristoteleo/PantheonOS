"""Tests for SessionLogManager."""

import pytest

from pantheon.internal.memory_system.session_log import SessionLogManager


class TestSessionLogManager:
    @pytest.fixture
    def manager(self, tmp_path):
        return SessionLogManager(tmp_path / "session-logs")

    def test_append_creates_file(self, manager):
        manager.append("sess-1", "delegation completed")
        assert manager.exists("sess-1")
        content = manager.read("sess-1")
        assert "delegation completed" in content

    def test_append_multiple(self, manager):
        manager.append("sess-1", "first entry")
        manager.append("sess-1", "second entry")
        content = manager.read("sess-1")
        assert "first entry" in content
        assert "second entry" in content

    def test_read_nonexistent(self, manager):
        assert manager.read("nonexistent") == ""

    def test_exists(self, manager):
        assert not manager.exists("sess-1")
        manager.append("sess-1", "test")
        assert manager.exists("sess-1")

    def test_safe_filename(self):
        assert SessionLogManager._safe_filename("abc-123") == "abc-123"
        assert SessionLogManager._safe_filename("a/b:c") == "a_b_c"
        assert len(SessionLogManager._safe_filename("x" * 200)) <= 80

    def test_separate_sessions(self, manager):
        manager.append("sess-1", "session 1 log")
        manager.append("sess-2", "session 2 log")
        assert "session 1" in manager.read("sess-1")
        assert "session 2" in manager.read("sess-2")
        assert "session 2" not in manager.read("sess-1")

    def test_timestamp_format(self, manager):
        manager.append("sess-1", "test entry")
        content = manager.read("sess-1")
        # Should contain "- YYYY-MM-DD HH:MM test entry"
        assert content.startswith("- 20")
        assert "test entry" in content
