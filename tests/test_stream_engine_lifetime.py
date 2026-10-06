"""The packaged Linux stream adapter retains and closes its owned engine."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('startup_failure', [False, True])
async def test_stream_cleanup_joins_engine_and_preserves_display_on_failure(monkeypatch, tmp_path, startup_failure):
    # Load the actual adapter beside the canonical Browser modules, just as the
    # Linux execution package does. Xpra is replaced by an owned child process;
    # this gate exercises ownership and the native save guard, not capture.
    spec = importlib.util.spec_from_file_location(
        'pantheon.apps.builtin.desktop._stream_lifetime_fixture',
        ROOT/'pantheon/apps/stream_runtime.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    monkeypatch.setattr(adapter.shutil, 'which', lambda name: '/fixture/'+name)
    monkeypatch.setenv('PANTHEON_PORT_STREAM', '18099')
    display_lock = (tmp_path/'display-lock').open('a')
    monkeypatch.setattr(adapter, 'reserve_display', lambda: (':299', display_lock))
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    engines = []
    original = adapter.BrowserEngine
    def engine_factory(**kwargs):
        engine = original(**kwargs)
        engines.append(engine)
        return engine
    monkeypatch.setattr(adapter, 'BrowserEngine', engine_factory)
    async def stage(self):
        self._xvfb_proc = process
        self._context = SimpleNamespace(close=AsyncMock(side_effect=[OSError('close failed'), None]))
        if startup_failure:
            raise RuntimeError('display startup failed')
    monkeypatch.setattr(BrowserEngine, 'ensure_native_stage', stage)
    native = ModuleType('pantheon.apps.builtin.desktop.qupath.native')
    native.NativeAppManager = lambda *a, **kw: SimpleNamespace(
        _executable=lambda: 'fixture', sessions={'one': SimpleNamespace(process=process)})
    monkeypatch.setitem(sys.modules, native.__name__, native)
    methods = {}
    ctx = SimpleNamespace(app_id='qupath', state_dir=tmp_path, workspace=tmp_path,
                          method=lambda method: methods.setdefault(method.__name__, method))
    def on_cleanup(callback):
        ctx.cleanup = callback
        return callback
    ctx.on_cleanup = on_cleanup
    try:
        if startup_failure:
            with pytest.raises(OSError, match='close failed'):
                await adapter.register(ctx)
        else:
            await adapter.register(ctx)
            # Native close callbacks can retire pages while their asynchronous
            # metadata is read. Inventory remains a bounded snapshot; the next
            # query must reflect their retirement instead of failing iteration.
            engine = engines[0]
            for page_id in ('first', 'second'):
                async def title(page_id=page_id):
                    engine.pages.pop(page_id)
                    return page_id
                engine.pages[page_id] = SimpleNamespace(
                    id=page_id, url='about:blank', title=title, width=640, height=480)
            inventory = await methods['browser_pages']()
            assert [page['page_id'] for page in inventory['pages']] == ['first', 'second']
            assert (await methods['browser_pages']())['pages'] == []
            with pytest.raises(RuntimeError, match='Save/Cancel'):
                await ctx.before_stop()
            process.terminate()
            process.wait(timeout=5)
            await ctx.before_stop()
            with pytest.raises(OSError, match='close failed'):
                await ctx.cleanup()
        assert not display_lock.closed
        assert engines[0]._thread.is_alive()
        await ctx.cleanup()
        assert display_lock.closed and process.poll() is not None
        assert engines[0]._loop.is_closed() and not engines[0]._thread.is_alive()
        await ctx.cleanup()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if hasattr(ctx, 'cleanup'):
            await ctx.cleanup()
        display_lock.close()
