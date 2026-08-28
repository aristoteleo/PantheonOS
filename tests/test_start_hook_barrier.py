"""The boot hook runs alongside the agent; the wait moved to its users.

The image's system paths are ephemeral, so an `apt-get install` from the
last session is gone and the hook has to run every boot. Running it BEFORE
the agent made "the hook has finished before the agent starts" true for
everyone, at the price of four seconds on every boot. Only the caller that
uses what the hook installs can observe that guarantee, so that is where
the waiting belongs.
"""

import asyncio

import pytest

from pantheon.utils import start_hook


@pytest.fixture(autouse=True)
def _markers(tmp_path, monkeypatch):
    monkeypatch.setattr(start_hook, "RUNNING_MARKER", tmp_path / "running")
    monkeypatch.setattr(start_hook, "DONE_MARKER", tmp_path / "done")
    monkeypatch.setattr(start_hook, "_warned", False)
    return tmp_path


def test_no_hook_means_no_waiting(_markers):
    """The common case — no hook at all — must cost nothing."""
    asyncio.run(asyncio.wait_for(start_hook.wait_for_start_hook(), timeout=0.5))


def test_a_finished_hook_does_not_delay_anything(_markers):
    (_markers / "running").touch()
    (_markers / "done").touch()
    asyncio.run(asyncio.wait_for(start_hook.wait_for_start_hook(), timeout=0.5))


def test_a_running_hook_is_waited_for(_markers):
    (_markers / "running").touch()

    async def run():
        async def finish_soon():
            await asyncio.sleep(0.2)
            (_markers / "done").touch()

        started = asyncio.get_event_loop().time()
        await asyncio.gather(finish_soon(), start_hook.wait_for_start_hook(5))
        return asyncio.get_event_loop().time() - started

    waited = asyncio.run(run())
    assert 0.15 < waited < 2.0, f"waited {waited:.2f}s"


def test_a_hook_that_never_finishes_does_not_hang_the_agent(_markers):
    """A wedged hook must not make the agent look wedged too."""
    (_markers / "running").touch()

    async def run():
        started = asyncio.get_event_loop().time()
        await start_hook.wait_for_start_hook(0.3)
        return asyncio.get_event_loop().time() - started

    waited = asyncio.run(run())
    assert waited < 2.0, f"waited {waited:.2f}s — the bound did not hold"
