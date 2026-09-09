"""Completion stays bounded and does not block notebook RPC/heartbeat handling."""
import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from pantheon.apps.builtin.notebook.jedi_integration import JediCodeIntelligence, EnhancedCompletionService


def test_completion_only_enriches_visible_function_candidates(monkeypatch):
    intelligence = JediCodeIntelligence()
    completions = []
    for index in range(300):
        candidate = MagicMock()
        candidate.name = f'item_{index}'
        candidate.type = 'module' if index < 25 else 'function'
        candidate.get_signatures.return_value = []
        completions.append(candidate)
    script = MagicMock()
    script.complete.return_value = completions
    monkeypatch.setattr(intelligence, '_get_jedi_script', lambda *args: (script, 1, 1))
    assert len(intelligence.get_completions('import m', 8, 'test')) == 50
    assert sum(c.get_signatures.call_count for c in completions) == 25
    assert all(not c.get_signatures.called for c in completions[50:])


@pytest.mark.parametrize('operation', ['get_completions', 'get_inspection'])
async def test_slow_analysis_does_not_block_event_loop(monkeypatch, operation):
    service = EnhancedCompletionService()
    started, release = threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        assert release.wait(3), 'event loop was blocked by code analysis'
        return [] if operation == 'get_completions' else {'found': False}
    monkeypatch.setattr(service.jedi_intelligence, operation, slow)
    task = asyncio.create_task(getattr(service, operation)('import matplotlib', 17, 'test'))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        # The loop can service other requests while Jedi is still working.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    assert (await task)['success'] is True
