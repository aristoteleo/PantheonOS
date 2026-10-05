"""App shutdown owns the Browser engine loop, tasks, processes and profile lock."""
import asyncio
import inspect
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine


@pytest.mark.asyncio
async def test_close_drains_calls_and_background_workers_despite_repeated_cancellation(tmp_path):
    engine = BrowserEngine(profile=tmp_path/'profile')
    started, release, background_started, background_release = (threading.Event() for _ in range(4))
    order = []
    async def background():
        background_started.set()
        await asyncio.to_thread(background_release.wait)
        order.append('background')
    async def command():
        engine._spawn(background())
        started.set()
        await asyncio.to_thread(release.wait)
        order.append('call')
    async def context_close():
        assert order == ['call', 'background']
        order.append('context')
    engine._context = SimpleNamespace(close=context_close)
    call = asyncio.create_task(engine.call(command()))
    close = None
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert await asyncio.to_thread(background_started.wait, 5)
        close = asyncio.create_task(engine.aclose())
        async with asyncio.timeout(5):
            while not engine._closing:
                await asyncio.sleep(.001)
        refused = asyncio.sleep(0)
        with pytest.raises(RuntimeError, match='closing'):
            await engine.call(refused)
        assert inspect.getcoroutinestate(refused) == inspect.CORO_CLOSED
        close.cancel()
        await asyncio.sleep(.01)
        close.cancel()
        assert not close.done() and engine._thread.is_alive()
        release.set()
        await call
        await asyncio.sleep(.01)
        assert not close.done() and 'context' not in order
        background_release.set()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert order == ['call', 'background', 'context']
        assert not engine._thread.is_alive() and engine._loop.is_closed()
        assert engine._closed and not engine._active_calls and not engine._background
        await asyncio.gather(engine.aclose(), engine.aclose())
    finally:
        release.set(); background_release.set()
        await asyncio.gather(call, *([close] if close else []), return_exceptions=True)
        await engine.aclose()


@pytest.mark.asyncio
async def test_cancelled_call_finalizer_is_joined_before_context_shutdown(tmp_path):
    engine = BrowserEngine(profile=tmp_path/'profile')
    started, finishing, finish = (threading.Event() for _ in range(3))
    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finishing.set()
            await asyncio.to_thread(finish.wait)
    call = asyncio.create_task(engine.call(operation()))
    close = None
    try:
        assert await asyncio.to_thread(started.wait, 5)
        call.cancel()
        with pytest.raises(asyncio.CancelledError): await call
        assert await asyncio.to_thread(finishing.wait, 5)
        close = asyncio.create_task(engine.aclose())
        await asyncio.sleep(.02)
        assert not close.done()
        finish.set()
        await close
        assert not engine._thread.is_alive()
    finally:
        finish.set()
        call.cancel()
        await asyncio.gather(call, *([close] if close else []), return_exceptions=True)
        await engine.aclose()


@pytest.mark.asyncio
async def test_failed_shutdown_retains_loop_process_and_profile_for_retry(tmp_path):
    engine = BrowserEngine(profile=tmp_path/'profile')
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        async def resources():
            engine._acquire_profile_lock(engine.profile)
            engine._xvfb_proc = process
            engine._context = SimpleNamespace(close=AsyncMock(side_effect=[OSError('close failed'), None]))
        await engine.call(resources())
        with pytest.raises(OSError, match='close failed'):
            await engine.aclose()
        assert engine._thread.is_alive() and not engine._loop.is_closed()
        assert engine._profile_lock_fd is not None and process.poll() is None
        await engine.aclose()
        assert not engine._thread.is_alive() and engine._loop.is_closed()
        assert engine._profile_lock_fd is None and process.poll() is not None
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=5)
        await engine.aclose()


@pytest.mark.asyncio
async def test_background_failure_does_not_abandon_other_work_or_owned_display(tmp_path):
    engine = BrowserEngine(profile=tmp_path/'profile')
    finished = threading.Event()
    async def fail():
        raise RuntimeError('background failed')
    async def work():
        await asyncio.sleep(.02)
        finished.set()
    async def start():
        engine._spawn(fail())
        engine._spawn(work())
        engine._dialog_task = engine._spawn(engine._dialog_keeper())
    try:
        await engine.call(start())
        await engine.aclose()
        assert finished.is_set() and not engine._thread.is_alive()
    finally:
        await engine.aclose()


@pytest.mark.asyncio
async def test_real_chromium_profiles_survive_close_while_other_engine_keeps_running(tmp_path):
    from playwright.async_api import async_playwright
    first = BrowserEngine(profile=tmp_path/'first')
    second = BrowserEngine(profile=tmp_path/'second')
    reopened = BrowserEngine(profile=tmp_path/'first')
    async def launch(engine):
        # Explicit headless capture fixture avoids changing installed browser
        # policies or starting a display on the user's desktop. The engine owns
        # real Playwright/Chromium and profile resources on its daemon loop.
        engine._acquire_profile_lock(engine.profile)
        engine._pw = await async_playwright().start()
        assert Path(engine._pw.chromium.executable_path).is_file()
        engine._context = await engine._pw.chromium.launch_persistent_context(
            str(engine.profile), headless=True, channel='chromium', args=['--no-sandbox'])
        page = await engine._context.new_page()
        await page.set_content('''<button id="test" onclick="this.textContent='clicked'">ready</button>''')
        return page
    try:
        page1, page2 = await asyncio.gather(first.call(launch(first)), second.call(launch(second)))
        await first.call(first._context.add_cookies([{'name': 'retained', 'value': 'yes',
            'domain': 'desktop.test', 'path': '/', 'expires': 4102444800}]))
        await first.call(page1.locator('#test').click())
        assert await first.call(page1.locator('#test').inner_text()) == 'clicked'
        assert first._profile_process_owners(first.profile)
        await first.aclose()
        assert first._loop.is_closed() and not first._thread.is_alive()
        assert not first._profile_process_owners(first.profile)
        assert await second.call(page2.locator('#test').inner_text()) == 'ready'
        assert second._profile_process_owners(second.profile)
        await reopened.call(launch(reopened))
        cookies = await reopened.call(reopened._context.cookies())
        assert any(c['name'] == 'retained' and c['value'] == 'yes' for c in cookies)
    finally:
        await asyncio.gather(first.aclose(), second.aclose(), reopened.aclose())
    for engine in (first, second, reopened):
        assert engine._closed and not engine._thread.is_alive() and engine._loop.is_closed()
        assert not engine._profile_process_owners(engine.profile)


@pytest.mark.asyncio
async def test_slow_start_retries_same_thread_and_closes_it(monkeypatch, tmp_path):
    engine = BrowserEngine(profile=tmp_path/'profile')
    release = threading.Event()
    original = asyncio.new_event_loop
    created = []
    def delayed_loop():
        release.wait(5)
        loop = original()
        created.append(loop)
        return loop
    monkeypatch.setattr(asyncio, 'new_event_loop', delayed_loop)
    monkeypatch.setattr(engine, '_THREAD_START_TIMEOUT', .02)
    try:
        with pytest.raises(RuntimeError, match='did not become ready'):
            await engine.call(asyncio.sleep(0))
        thread = engine._thread
        with pytest.raises(RuntimeError, match='did not become ready'):
            await engine.call(asyncio.sleep(0))
        assert engine._thread is thread and thread.is_alive()
        with pytest.raises(RuntimeError, match='did not become ready'):
            await engine.aclose()
        assert engine._thread is thread
        release.set()
        assert await asyncio.to_thread(engine._ready.wait, 5)
        await engine.aclose()
        assert len(created) == 1 and created[0].is_closed()
        assert not thread.is_alive()
    finally:
        release.set()
        await engine.aclose()


@pytest.mark.asyncio
async def test_cancellation_before_loop_admission_disposes_coroutine(tmp_path):
    engine = BrowserEngine(profile=tmp_path/'profile')
    held, release = threading.Event(), threading.Event()
    await engine.call(asyncio.sleep(0))
    def hold_loop():
        held.set()
        release.wait(5)
    engine._loop.call_soon_threadsafe(hold_loop)
    supplied = asyncio.sleep(0)
    call = None
    try:
        assert await asyncio.to_thread(held.wait, 5)
        call = asyncio.create_task(engine.call(supplied))
        await asyncio.sleep(.01)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        release.set()
        await engine.aclose()
        assert inspect.getcoroutinestate(supplied) == inspect.CORO_CLOSED
        assert not engine._active_calls and not engine._thread.is_alive()
    finally:
        release.set()
        if call:
            await asyncio.gather(call, return_exceptions=True)
        await engine.aclose()
