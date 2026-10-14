"""A stopped Desktop leaves App data the next revision can copy: no links."""
import os
import socket
from unittest.mock import AsyncMock

import pytest

from apps.desktop.browser import BrowserEngine


@pytest.mark.asyncio
async def test_stopping_the_desktop_closes_its_browser(monkeypatch):
    from apps.desktop.toolset import DesktopToolSet
    engine = AsyncMock()
    monkeypatch.setattr(BrowserEngine, '_instance', engine)
    await DesktopToolSet('copyable').cleanup()
    engine.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_shutdown_clears_singleton_links_of_an_exited_chromium(tmp_path):
    profile = tmp_path / 'browser-profile'
    profile.mkdir()
    # A Chromium of this host that is no longer running (the PID is free).
    dead = 2 ** 22 - 7
    while True:
        try:
            os.kill(dead, 0)
            dead -= 1
        except ProcessLookupError:
            break
        except PermissionError:
            dead -= 1
    os.symlink(f'{socket.gethostname()}-{dead}', profile / 'SingletonLock')
    os.symlink('16312184935291402847', profile / 'SingletonCookie')
    engine = BrowserEngine(profile=profile)
    await engine.shutdown()
    assert not any(p.is_symlink() for p in profile.iterdir())
