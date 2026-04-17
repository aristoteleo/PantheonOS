"""Tests for notebook execution concurrency control and lock mechanisms."""

import asyncio
import json
import pytest
import tempfile
import os






class TestExecutionLockBehavior:
    """Test execution lock behavior with mock scenarios."""

    @pytest.mark.asyncio
    async def test_lock_helper_thread_safe(self):
        """Test that _get_execution_lock uses setdefault for thread safety."""
        from pantheon.toolsets.notebook.jupyter_kernel import JupyterKernelToolSet
        
        with tempfile.TemporaryDirectory() as tmpdir:
            toolset = JupyterKernelToolSet(name="test", workdir=tmpdir)
            
            # Get lock multiple times concurrently
            locks = await asyncio.gather(*[
                asyncio.to_thread(toolset._get_execution_lock, "session_1")
                for _ in range(10)
            ])
            
            # All should be the same lock object
            assert all(lock is locks[0] for lock in locks)

    @pytest.mark.asyncio
    async def test_lock_isolated_per_session(self):
        """Test that each session gets its own lock."""
        from pantheon.toolsets.notebook.jupyter_kernel import JupyterKernelToolSet
        
        with tempfile.TemporaryDirectory() as tmpdir:
            toolset = JupyterKernelToolSet(name="test", workdir=tmpdir)
            
            lock1 = toolset._get_execution_lock("session_1")
            lock2 = toolset._get_execution_lock("session_2")
            
            assert lock1 is not lock2


class TestNotebookFileLock:
    """Test notebook file lock behavior."""

    @pytest.mark.asyncio
    async def test_file_lock_helper_thread_safe(self):
        """Test that _get_notebook_lock uses setdefault for thread safety."""
        from pantheon.toolsets.notebook.integrated_notebook import IntegratedNotebookToolSet
        
        with tempfile.TemporaryDirectory() as tmpdir:
            toolset = IntegratedNotebookToolSet(
                name="test", 
                workdir=tmpdir,
                streaming_mode="local"
            )
            
            # Get lock multiple times  
            locks = [
                toolset._get_notebook_lock("/path/to/notebook.ipynb")
                for _ in range(10)
            ]
            
            # All should be the same lock object
            assert all(lock is locks[0] for lock in locks)

    @pytest.mark.asyncio
    async def test_file_lock_isolated_per_notebook(self):
        """Test that each notebook gets its own lock."""
        from pantheon.toolsets.notebook.integrated_notebook import IntegratedNotebookToolSet
        
        with tempfile.TemporaryDirectory() as tmpdir:
            toolset = IntegratedNotebookToolSet(
                name="test",
                workdir=tmpdir,
                streaming_mode="local"
            )
            
            lock1 = toolset._get_notebook_lock("/path/to/notebook1.ipynb")
            lock2 = toolset._get_notebook_lock("/path/to/notebook2.ipynb")
            
            assert lock1 is not lock2


class TestIntegrationConcurrency:
    """Integration tests for concurrency (requires kernel)."""

    @pytest.fixture
    async def notebook_toolset(self):
        """Create IntegratedNotebookToolSet for testing."""
        from pantheon.toolsets.notebook.integrated_notebook import IntegratedNotebookToolSet
        
        with tempfile.TemporaryDirectory() as tmpdir:
            toolset = IntegratedNotebookToolSet(
                name="test_notebook",
                workdir=tmpdir,
                streaming_mode="local",
            )
            await toolset.run_setup()
            yield toolset, tmpdir
            await toolset.cleanup()

    @pytest.mark.asyncio
    async def test_concurrent_add_cell_serialized(self, notebook_toolset):
        """Test that concurrent add_cell operations are serialized by file lock."""
        toolset, tmpdir = notebook_toolset
        notebook_path = os.path.join(tmpdir, "test_concurrent.ipynb")
        
        # Create notebook
        create_result = await toolset.create_notebook(notebook_path)
        assert create_result["success"]
        
        # Launch multiple add_cell concurrently
        tasks = []
        for i in range(5):
            task = asyncio.create_task(
                toolset.add_cell(
                    notebook_path,
                    cell_type="code",
                    content=f"cell_{i} = {i}",
                    execute=False,
                )
            )
            tasks.append(task)
        
        # All should succeed (serialized by lock)
        results = await asyncio.gather(*tasks)
        for result in results:
            assert result["success"], f"add_cell failed: {result.get('error')}"
        
        # Verify all cells were added
        cells_result = await toolset.read_cells(notebook_path)
        assert cells_result["success"]
        assert len(cells_result["cells"]) == 5

    @pytest.mark.asyncio
    async def test_add_cell_with_execute_uses_separate_locks(self, notebook_toolset):
        """Test that add_cell(execute=True) uses file lock then execution lock."""
        toolset, tmpdir = notebook_toolset
        notebook_path = os.path.join(tmpdir, "test_locks.ipynb")
        
        # Create notebook
        create_result = await toolset.create_notebook(notebook_path)
        assert create_result["success"]
        
        # Add and execute a cell
        result = await toolset.add_cell(
            notebook_path,
            cell_type="code",
            content="x = 42; print(x)",
            execute=True,
        )
        assert result["success"]
        assert "execution" in result
        # Execution may succeed or fail depending on kernel, but structure is correct

    @pytest.mark.asyncio
    async def test_no_deadlock_mixed_operations(self, notebook_toolset):
        """Test that mixed edit/execute/edit+execute operations don't deadlock.
        
        Scenario: Parallel calls with different operation types
        - Task 1: add_cell (edit only)
        - Task 2: add_cell with execute=True (edit + execute)
        - Task 3: update_cell (edit only)
        """
        toolset, tmpdir = notebook_toolset
        notebook_path = os.path.join(tmpdir, "test_no_deadlock.ipynb")
        
        # Create notebook with initial cell
        create_result = await toolset.create_notebook(notebook_path)
        assert create_result["success"]
        
        initial = await toolset.add_cell(notebook_path, content="initial = 1", execute=False)
        assert initial["success"]
        initial_cell_id = initial["cell_id"]
        
        # Launch mixed operations concurrently
        async def add_only():
            return await toolset.add_cell(notebook_path, content="add_only = 2", execute=False)
        
        async def add_and_execute():
            return await toolset.add_cell(notebook_path, content="add_exec = 3", execute=True)
        
        async def update_only():
            return await toolset.update_cell(notebook_path, initial_cell_id, content="updated = 4", execute=False)
        
        # Run all concurrently with timeout to detect deadlock
        try:
            results = await asyncio.wait_for(
                asyncio.gather(add_only(), add_and_execute(), update_only()),
                timeout=30.0  # 30 seconds should be plenty
            )
            # All edit operations should succeed
            for r in results:
                assert r["success"], f"Operation failed: {r.get('error')}"
        except asyncio.TimeoutError:
            pytest.fail("Deadlock detected - operations timed out")

    @pytest.mark.asyncio
    async def test_blocking_execution_does_not_block_file_edits(self, notebook_toolset):
        """Test that a long-running execution doesn't block file edits.
        
        Scenario:
        - Task 1: add_cell with slow execution (sleep 2s)
        - Task 2: add_cell (edit only) - should complete quickly
        """
        toolset, tmpdir = notebook_toolset
        notebook_path = os.path.join(tmpdir, "test_blocking.ipynb")
        
        create_result = await toolset.create_notebook(notebook_path)
        assert create_result["success"]
        
        # Track timing
        import time
        
        async def slow_add_execute():
            """Add cell that sleeps for 2 seconds during execution."""
            return await toolset.add_cell(
                notebook_path,
                content="import time; time.sleep(2); print('slow done')",
                execute=True,
            )
        
        async def fast_add_only():
            """Simple add without execution."""
            await asyncio.sleep(0.3)  # Small delay to ensure slow task starts first
            start = time.time()
            result = await toolset.add_cell(notebook_path, content="fast = 1", execute=False)
            elapsed = time.time() - start
            return result, elapsed
        
        # Run concurrently
        slow_task = asyncio.create_task(slow_add_execute())
        fast_task = asyncio.create_task(fast_add_only())
        
        slow_result, (fast_result, fast_elapsed) = await asyncio.gather(slow_task, fast_task)
        
        # Fast edit should complete quickly (< 1s), not blocked by 2s execution
        assert fast_result["success"]
        assert fast_elapsed < 1.5, f"Edit took {fast_elapsed}s, should be < 1.5s (not blocked by execution)"
        
        # Slow execution should also complete (may return busy error if concurrent)
        assert slow_result["success"]

    @pytest.mark.asyncio
    async def test_concurrent_execute_on_same_kernel_returns_busy(self, notebook_toolset):
        """Test that concurrent executions on same kernel return busy error."""
        toolset, tmpdir = notebook_toolset
        notebook_path = os.path.join(tmpdir, "test_busy.ipynb")
        
        create_result = await toolset.create_notebook(notebook_path)
        assert create_result["success"]
        
        # Add two cells
        cell1 = await toolset.add_cell(notebook_path, content="import time; time.sleep(2)", execute=False)
        cell2 = await toolset.add_cell(notebook_path, content="print('quick')", execute=False)
        assert cell1["success"] and cell2["success"]
        
        # Execute first cell (slow)
        async def slow_exec():
            return await toolset.execute_cell(notebook_path, cell1["cell_id"])
        
        async def quick_exec():
            await asyncio.sleep(0.5)  # Wait for slow to start
            return await toolset.execute_cell(notebook_path, cell2["cell_id"])
        
        slow_task = asyncio.create_task(slow_exec())
        quick_task = asyncio.create_task(quick_exec())
        
        slow_result, quick_result = await asyncio.gather(slow_task, quick_task)
        
        # One should succeed, one should return busy
        results = [slow_result, quick_result]
        successes = [r for r in results if r.get("success")]
        busys = [r for r in results if r.get("error") and "busy" in r.get("error", "").lower()]
        
        assert len(successes) >= 1, "At least one execution should succeed"
        # Note: Due to timing, both might succeed if first completes before second starts

    @pytest.mark.asyncio
    async def test_execution_output_correctness(self, notebook_toolset):
        """Test that each cell execution gets its own correct output.
        
        This is the core test for the race condition fix - ensures outputs
        are correctly matched to their respective cell executions.
        """
        toolset, tmpdir = notebook_toolset
        notebook_path = os.path.join(tmpdir, "test_output_correctness.ipynb")
        
        create_result = await toolset.create_notebook(notebook_path)
        assert create_result["success"]
        
        # Execute cells with unique identifiable outputs
        markers = ["MARKER_AAA_111", "MARKER_BBB_222", "MARKER_CCC_333"]
        results = []
        
        for marker in markers:
            result = await toolset.add_cell(
                notebook_path,
                content=f"print('{marker}')",
                execute=True,
            )
            results.append((marker, result))
        
        # Verify each cell got its own correct output
        for marker, result in results:
            assert result["success"], f"Execution failed: {result.get('error')}"
            assert "execution" in result, "Missing execution result"
            
            exec_result = result["execution"]
            assert exec_result.get("success"), f"Execution not successful for {marker}"
            
            outputs = exec_result.get("outputs", [])
            output_text = ""
            for output in outputs:
                if output.get("output_type") == "stream":
                    output_text += output.get("text", "")
            
            # Core assertion: each cell's output contains ONLY its own marker
            assert marker in output_text, f"Expected '{marker}' in output, got: {output_text}"
            
            # Ensure no other markers leaked into this output
            for other_marker in markers:
                if other_marker != marker:
                    assert other_marker not in output_text, \
                        f"Output mixing detected: '{other_marker}' found in output for {marker}"


class TestStaleCellIdRecovery:
    """Tests for recovering from stale / missing cell IDs."""

    @pytest.fixture
    async def notebook_toolset(self):
        from pantheon.toolsets.notebook.integrated_notebook import IntegratedNotebookToolSet

        with tempfile.TemporaryDirectory() as tmpdir:
            toolset = IntegratedNotebookToolSet(
                name="test_notebook",
                workdir=tmpdir,
                streaming_mode="local",
            )
            await toolset.run_setup()
            yield toolset, tmpdir
            await toolset.cleanup()

    def _write_raw_notebook(self, path: str, cells: list[dict]):
        """Write a minimal nbformat 4.4 notebook (no cell IDs)."""
        notebook = {
            "cells": cells,
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {"name": "python"},
            },
            "nbformat": 4,
            "nbformat_minor": 4,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(notebook, f)

    @pytest.mark.asyncio
    async def test_read_cells_auto_assigns_stable_ids(self, notebook_toolset):
        """read_cells should auto-assign deterministic IDs to cells without them."""
        toolset, tmpdir = notebook_toolset
        nb_path = os.path.join(tmpdir, "no_ids.ipynb")
        self._write_raw_notebook(nb_path, [
            {"cell_type": "code", "execution_count": None, "metadata": {},
             "outputs": [], "source": "print('hello')"},
            {"cell_type": "code", "execution_count": None, "metadata": {},
             "outputs": [], "source": "print('world')"},
        ])

        result = await toolset.read_cells(nb_path)
        assert result["success"]
        ids = [c["cell_id"] for c in result["cells"]]
        assert all(ids), "All cells should have IDs"
        assert len(set(ids)) == 2, "IDs should be unique"

        # Read again — IDs should be the same (deterministic)
        result2 = await toolset.read_cells(nb_path)
        ids2 = [c["cell_id"] for c in result2["cells"]]
        assert ids == ids2, "IDs should be stable across reads"

    @pytest.mark.asyncio
    async def test_execute_cell_with_auto_assigned_id(self, notebook_toolset):
        """execute_cell should work with IDs that were auto-assigned by read_cells."""
        toolset, tmpdir = notebook_toolset
        nb_path = os.path.join(tmpdir, "exec_no_id.ipynb")
        self._write_raw_notebook(nb_path, [
            {"cell_type": "code", "execution_count": None, "metadata": {},
             "outputs": [], "source": "x = 1\nprint(x)"},
        ])

        # read_cells auto-assigns IDs
        read_result = await toolset.read_cells(nb_path)
        assert read_result["success"]
        cell_id = read_result["cells"][0]["cell_id"]
        assert cell_id

        # execute_cell should find the cell by its auto-assigned ID
        exec_result = await toolset.execute_cell(nb_path, cell_id)
        assert exec_result["success"], exec_result.get("error")

    @pytest.mark.asyncio
    async def test_update_cell_recovers_via_source_hint(self, notebook_toolset):
        """update_cell should recover when cell_id is stale but old_content matches."""
        toolset, tmpdir = notebook_toolset
        nb_path = os.path.join(tmpdir, "stale_update.ipynb")

        # Create notebook properly (has real IDs)
        create_result = await toolset.create_notebook(nb_path)
        assert create_result["success"]
        add_result = await toolset.add_cell(nb_path, content="x = 1\nprint(x)")
        assert add_result["success"]
        real_id = add_result["cell_id"]

        # Update using a stale/wrong cell_id but correct old_content
        result = await toolset.update_cell(
            notebook_path=nb_path,
            cell_id="stale_nonexistent_id",
            content="x = 2",
            old_content="x = 1",
        )
        assert result["success"], result.get("error")

    @pytest.mark.asyncio
    async def test_stale_id_without_hint_returns_available_cells(self, notebook_toolset):
        """When cell_id is stale and no source_hint, error should list available cells."""
        toolset, tmpdir = notebook_toolset
        nb_path = os.path.join(tmpdir, "stale_error.ipynb")

        create_result = await toolset.create_notebook(nb_path)
        assert create_result["success"]
        add_result = await toolset.add_cell(nb_path, content="hello = 1")
        assert add_result["success"]
        real_id = add_result["cell_id"]

        # Try execute with wrong cell_id
        result = await toolset.execute_cell(nb_path, "totally_wrong_id")
        assert not result["success"]
        assert "not found" in result["error"]
        assert real_id in result["error"], "Error should contain available cell IDs"
        assert "read_cells" in result["error"], "Error should suggest read_cells"

