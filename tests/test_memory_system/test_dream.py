"""Tests for dream consolidation: gating, locking, and consolidation."""

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.dream import (
    ConsolidationLock,
    DreamConsolidator,
    DreamGate,
)


class TestConsolidationLock:
    def test_read_no_lock(self, store):
        lock = ConsolidationLock(store.durable_dir)
        assert lock.read_last_consolidated_at() == 0.0

    def test_acquire_and_read(self, store):
        lock = ConsolidationLock(store.durable_dir)
        prior = lock.try_acquire()
        assert prior is not None
        assert lock.lock_path.exists()

    def test_acquire_blocks_second(self, store):
        lock = ConsolidationLock(store.durable_dir)
        lock.try_acquire()
        lock2 = ConsolidationLock(store.durable_dir)
        assert lock2.try_acquire() is None

    def test_release_updates_mtime(self, store):
        lock = ConsolidationLock(store.durable_dir)
        lock.try_acquire()
        time.sleep(0.1)
        lock.release()
        assert lock.read_last_consolidated_at() > 0

    def test_rollback_to_zero(self, store):
        lock = ConsolidationLock(store.durable_dir)
        lock.try_acquire()
        lock.rollback(0.0)
        assert not lock.lock_path.exists()

    def test_stale_lock_reclaimable(self, store):
        lock = ConsolidationLock(store.durable_dir)
        lock.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock.lock_path.write_text("99999999")
        old_time = time.time() - 7200
        os.utime(lock.lock_path, (old_time, old_time))
        assert lock.try_acquire() is not None


class TestDreamGate:
    @pytest.mark.asyncio
    async def test_should_dream_when_conditions_met(self, store):
        gate = DreamGate(store, {"dream_min_hours": 0, "dream_min_sessions": 0})
        gate._last_scan_time = 0
        assert await gate.should_dream() is not None

    @pytest.mark.asyncio
    async def test_force_bypasses_gates(self, store):
        gate = DreamGate(store, {"dream_min_hours": 999, "dream_min_sessions": 999})
        assert await gate.should_dream(force=True) is not None

    def test_increment_session(self, store):
        gate = DreamGate(store, {"dream_min_hours": 24, "dream_min_sessions": 5})
        assert gate._session_counter == 0
        gate.increment_session()
        gate.increment_session()
        assert gate._session_counter == 2


class TestDreamConsolidator:
    @pytest.mark.asyncio
    async def test_consolidate_success(self, populated_store):
        consolidator = DreamConsolidator(populated_store, model="gpt-4o-mini")

        mock_resp = MagicMock()
        mock_resp.content = "Consolidated 2 memory files. Merged duplicates."

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_resp)

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await consolidator.consolidate()
            assert result.success

    @pytest.mark.asyncio
    async def test_consolidate_failure(self, populated_store):
        consolidator = DreamConsolidator(populated_store)

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=Exception("err"))

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await consolidator.consolidate()
            assert not result.success

    def test_build_prompt(self, populated_store):
        consolidator = DreamConsolidator(populated_store)
        prompt = consolidator._build_prompt()
        assert "MEMORY.md" in prompt
        assert "Memory files" in prompt
