"""Tests for background task support (pantheon/background.py and agent integration)."""

import asyncio
import io
import time

import pytest

from pantheon.background import (
    BackgroundTask,
    BackgroundTaskManager,
    _bg_output_buffer,
    _bg_report,
    _install_print_hook,
)


# =============================================================================
# Print hook tests
# =============================================================================


class TestPrintHook:
    def setup_method(self):
        _install_print_hook()

    def test_print_without_buffer(self, capsys):
        """When no contextvar buffer is set, print behaves normally."""
        print("hello")
        captured = capsys.readouterr()
        assert "hello" in captured.out

    def test_print_with_buffer(self):
        """When contextvar buffer is set, print output is captured."""
        buf = []
        token = _bg_output_buffer.set(buf)
        try:
            print("captured line")
            assert "captured line" in buf
        finally:
            _bg_output_buffer.reset(token)

    def test_print_with_file_kwarg_not_captured(self):
        """print(file=...) should NOT be captured (only stdout prints)."""
        buf = []
        token = _bg_output_buffer.set(buf)
        try:
            sio = io.StringIO()
            print("to file", file=sio)
            assert len(buf) == 0
            assert "to file" in sio.getvalue()
        finally:
            _bg_output_buffer.reset(token)

    def test_multiline_print(self):
        """Multi-arg print is captured as single line."""
        buf = []
        token = _bg_output_buffer.set(buf)
        try:
            print("a", "b", "c")
            assert "a b c" in buf
        finally:
            _bg_output_buffer.reset(token)

    def test_bg_report_with_buffer(self):
        """_bg_report appends to the active background buffer."""
        buf = []
        token = _bg_output_buffer.set(buf)
        try:
            _bg_report("progress 50%")
            _bg_report("progress 100%")
            assert buf == ["progress 50%", "progress 100%"]
        finally:
            _bg_output_buffer.reset(token)

    def test_bg_report_without_buffer(self):
        """_bg_report is a no-op when no buffer is active."""
        _bg_report("should be ignored")
        # No error, no side effects


# =============================================================================
# BackgroundTaskManager tests
# =============================================================================


class TestBackgroundTaskManager:
    @pytest.fixture
    def manager(self):
        return BackgroundTaskManager()

    @pytest.mark.asyncio
    async def test_start_and_complete(self, manager):
        """Start a task, let it complete, check status."""

        async def _work():
            await asyncio.sleep(0.05)
            return "done"

        bg = manager.start("test_tool", "tc_1", {"key": "val"}, _work())
        assert bg.status == "running"
        assert bg.task_id == "bg_1"
        assert bg.tool_name == "test_tool"
        assert bg.source == "explicit"

        await asyncio.sleep(0.2)

        assert bg.status == "completed"
        assert bg.result == "done"
        assert bg.completed_at is not None

    @pytest.mark.asyncio
    async def test_start_and_fail(self, manager):
        """Task that raises an exception should be marked failed."""

        async def _fail():
            raise ValueError("boom")

        bg = manager.start("fail_tool", "tc_2", {}, _fail())
        await asyncio.sleep(0.2)

        assert bg.status == "failed"
        assert "boom" in bg.error

    @pytest.mark.asyncio
    async def test_cancel(self, manager):
        """Cancel a running task."""

        async def _long():
            await asyncio.sleep(10)

        bg = manager.start("long_tool", "tc_3", {}, _long())
        assert manager.cancel(bg.task_id) is True
        await asyncio.sleep(0.1)

        assert bg.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, manager):
        assert manager.cancel("bg_999") is False

    @pytest.mark.asyncio
    async def test_cancel_already_done(self, manager):

        async def _quick():
            return 42

        bg = manager.start("quick", "tc_4", {}, _quick())
        await asyncio.sleep(0.1)
        assert bg.status == "completed"
        assert manager.cancel(bg.task_id) is False

    @pytest.mark.asyncio
    async def test_adopt(self, manager):
        """Adopt an existing asyncio.Task."""

        async def _work():
            await asyncio.sleep(0.05)
            return "adopted_result"

        existing = asyncio.create_task(_work())
        pre_buffer = ["line1", "line2"]

        bg = manager.adopt("adopted_tool", "tc_5", {}, existing, pre_buffer)
        assert bg.source == "timeout"
        assert bg.output_lines is pre_buffer  # same list object
        assert manager._is_adopted(existing)

        await asyncio.sleep(0.2)
        assert bg.status == "completed"
        assert bg.result == "adopted_result"

    @pytest.mark.asyncio
    async def test_adopt_with_empty_buffer(self, manager):
        """adopt() with empty list should still use it (not default)."""

        async def _work():
            return "ok"

        existing = asyncio.create_task(_work())
        empty_buf = []

        bg = manager.adopt("tool", "tc", {}, existing, empty_buf)
        assert bg.output_lines is empty_buf  # same object, not a new list

        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_adopt_output_continuity(self, manager):
        """After adoption, contextvar buffer IS bg_task.output_lines."""
        output_buf = []

        async def _work():
            await asyncio.sleep(0.05)
            print("after adopt")
            await asyncio.sleep(0.05)
            return "done"

        token = _bg_output_buffer.set(output_buf)
        try:
            existing = asyncio.create_task(_work())
        finally:
            _bg_output_buffer.reset(token)

        bg = manager.adopt("tool", "tc", {}, existing, output_buf)
        assert bg.output_lines is output_buf

        await asyncio.sleep(0.3)
        assert bg.status == "completed"
        assert any("after adopt" in line for line in bg.output_lines)

    @pytest.mark.asyncio
    async def test_list_and_get(self, manager):

        async def _noop():
            return None

        bg1 = manager.start("t1", "tc_a", {}, _noop())
        bg2 = manager.start("t2", "tc_b", {}, _noop())

        assert len(manager.list_tasks()) == 2
        assert manager.get(bg1.task_id) is bg1
        assert manager.get("bg_999") is None

        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_to_summary(self, manager):

        async def _work():
            return {"key": "value"}

        bg = manager.start("sum_tool", "tc_s", {"arg": 1}, _work())
        await asyncio.sleep(0.1)

        summary = manager.to_summary(bg)
        assert summary["task_id"] == bg.task_id
        assert summary["status"] == "completed"
        assert isinstance(summary["elapsed_seconds"], float)
        assert isinstance(summary["recent_output"], list)

    @pytest.mark.asyncio
    async def test_to_summary_truncates_result(self, manager):

        async def _big():
            return "x" * 3000

        bg = manager.start("big_tool", "tc_big", {}, _big())
        await asyncio.sleep(0.1)

        summary = manager.to_summary(bg)
        assert len(summary["result"]) <= 2020

    @pytest.mark.asyncio
    async def test_eviction(self):
        manager = BackgroundTaskManager(max_retained=3)

        async def _noop():
            return None

        for i in range(5):
            manager.start(f"tool_{i}", f"tc_{i}", {}, _noop())

        await asyncio.sleep(0.2)
        assert len(manager._tasks) <= 3

    @pytest.mark.asyncio
    async def test_cleanup(self, manager):

        async def _long():
            await asyncio.sleep(100)

        bg1 = manager.start("t1", "tc_1", {}, _long())
        bg2 = manager.start("t2", "tc_2", {}, _long())

        await manager.cleanup()

        assert bg1.status == "cancelled"
        assert bg2.status == "cancelled"

    @pytest.mark.asyncio
    async def test_print_capture_in_start(self, manager):
        """print() inside a started task is captured via print hook."""

        async def _print_work():
            print("progress 50%")
            await asyncio.sleep(0.01)
            print("progress 100%")
            return "done"

        bg = manager.start("print_tool", "tc_p", {}, _print_work())
        await asyncio.sleep(0.2)

        assert bg.status == "completed"
        assert any("50%" in line for line in bg.output_lines)
        assert any("100%" in line for line in bg.output_lines)

    @pytest.mark.asyncio
    async def test_bg_report_in_started_task(self, manager):
        """_bg_report() inside a started task appends to output_lines."""

        async def _report_work():
            _bg_report("[step] initializing")
            await asyncio.sleep(0.01)
            _bg_report("[step] processing")
            await asyncio.sleep(0.01)
            _bg_report("[step] done")
            return "ok"

        bg = manager.start("report_tool", "tc_r", {}, _report_work())
        await asyncio.sleep(0.2)

        assert bg.status == "completed"
        assert any("initializing" in line for line in bg.output_lines)
        assert any("processing" in line for line in bg.output_lines)
        assert any("done" in line for line in bg.output_lines)

    @pytest.mark.asyncio
    async def test_is_adopted_false_for_started(self, manager):

        async def _noop():
            return None

        bg = manager.start("t", "tc", {}, _noop())
        assert not manager._is_adopted(bg.asyncio_task)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_counter_increments(self, manager):

        async def _noop():
            return None

        bg1 = manager.start("t1", "tc_1", {}, _noop())
        bg2 = manager.start("t2", "tc_2", {}, _noop())
        bg3 = manager.start("t3", "tc_3", {}, _noop())

        assert bg1.task_id == "bg_1"
        assert bg2.task_id == "bg_2"
        assert bg3.task_id == "bg_3"

        await asyncio.sleep(0.1)


# =============================================================================
# Notification queue tests
# =============================================================================


class TestNotificationQueue:
    @pytest.fixture
    def manager(self):
        return BackgroundTaskManager(max_retained=50)

    def test_drain_empty(self, manager):
        """drain_notifications() returns empty list when nothing queued."""
        assert manager.drain_notifications() == []

    @pytest.mark.asyncio
    async def test_on_task_done_queues_notification(self, manager):
        """Completed task is automatically queued for notification."""

        async def _work():
            return "result"

        bg = manager.start("tool_a", "tc_1", {}, _work())
        await asyncio.sleep(0.1)

        assert bg.status == "completed"
        notifs = manager.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0].task_id == bg.task_id

    @pytest.mark.asyncio
    async def test_drain_clears_queue(self, manager):
        """drain_notifications() clears the queue after returning."""

        async def _work():
            return "ok"

        manager.start("tool_a", "tc_1", {}, _work())
        await asyncio.sleep(0.1)

        first = manager.drain_notifications()
        assert len(first) == 1

        second = manager.drain_notifications()
        assert second == []

    @pytest.mark.asyncio
    async def test_failed_task_queues_notification(self, manager):
        """Failed tasks are also queued for notification."""

        async def _fail():
            raise ValueError("boom")

        bg = manager.start("tool_b", "tc_2", {}, _fail())
        await asyncio.sleep(0.1)

        assert bg.status == "failed"
        notifs = manager.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0].status == "failed"

    @pytest.mark.asyncio
    async def test_cancelled_task_queues_notification(self, manager):
        """Cancelled tasks are also queued for notification."""

        async def _long():
            await asyncio.sleep(100)

        bg = manager.start("tool_c", "tc_3", {}, _long())
        manager.cancel(bg.task_id)
        await asyncio.sleep(0.1)

        notifs = manager.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0].status == "cancelled"

    @pytest.mark.asyncio
    async def test_multiple_tasks_queued(self, manager):
        """Multiple completed tasks accumulate in notification queue."""

        async def _work(val):
            return val

        bg1 = manager.start("t1", "tc_1", {}, _work("a"))
        bg2 = manager.start("t2", "tc_2", {}, _work("b"))
        bg3 = manager.start("t3", "tc_3", {}, _work("c"))
        await asyncio.sleep(0.1)

        notifs = manager.drain_notifications()
        assert len(notifs) == 3
        ids = {n.task_id for n in notifs}
        assert ids == {bg1.task_id, bg2.task_id, bg3.task_id}

    @pytest.mark.asyncio
    async def test_adopted_task_queues_notification(self, manager):
        """Adopted tasks also queue notifications on completion."""

        async def _work():
            return "adopted_result"

        existing = asyncio.create_task(_work())
        bg = manager.adopt("tool_d", "tc_4", {}, existing)
        await asyncio.sleep(0.1)

        assert bg.status == "completed"
        notifs = manager.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0].task_id == bg.task_id


# =============================================================================
# Contextvar isolation tests
# =============================================================================


class TestContextvarIsolation:
    def setup_method(self):
        _install_print_hook()

    @pytest.mark.asyncio
    async def test_separate_tasks_dont_interfere(self):
        """Two concurrent asyncio tasks should have separate buffers."""
        buf_a = []
        buf_b = []

        async def task_a():
            token = _bg_output_buffer.set(buf_a)
            try:
                print("from_a")
                await asyncio.sleep(0.05)
                print("from_a_2")
            finally:
                _bg_output_buffer.reset(token)

        async def task_b():
            token = _bg_output_buffer.set(buf_b)
            try:
                print("from_b")
                await asyncio.sleep(0.05)
                print("from_b_2")
            finally:
                _bg_output_buffer.reset(token)

        await asyncio.gather(
            asyncio.create_task(task_a()),
            asyncio.create_task(task_b()),
        )

        assert all("from_a" in line for line in buf_a)
        assert all("from_b" in line for line in buf_b)
        assert not any("from_b" in line for line in buf_a)
        assert not any("from_a" in line for line in buf_b)


# =============================================================================
# Integration: Agent background tools registration
# =============================================================================


class TestAgentBackgroundToolsRegistration:
    def test_bg_tools_registered(self):
        from pantheon.agent import Agent

        agent = Agent(name="test", instructions="test")
        assert "run_in_background" in agent._base_functions
        assert "get_background_task" in agent._base_functions
        assert "cancel_background_task" in agent._base_functions

    def test_bg_manager_exists(self):
        from pantheon.agent import Agent

        agent = Agent(name="test", instructions="test")
        assert isinstance(agent._bg_manager, BackgroundTaskManager)

    def test_tool_output_buffers_initialized(self):
        from pantheon.agent import Agent

        agent = Agent(name="test", instructions="test")
        assert isinstance(agent._tool_output_buffers, dict)
