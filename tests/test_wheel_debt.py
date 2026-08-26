"""Wheels accumulate and cancel; they never queue.

The bug this pins: scrolling down and then flicking up kept scrolling
down for a while, because every wheel event was played back in order.
"""

import asyncio

import pytest

from pantheon.toolsets.desktop.browser import (
    WHEEL_MAX_DEBT_PX,
    WHEEL_STEP_PX,
    BrowserEngine,
    _clamp_debt,
)


class FakeMouse:
    def __init__(self):
        self.wheels = []

    async def move(self, x, y):
        pass

    async def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class FakePage:
    def __init__(self):
        self.mouse = FakeMouse()


class FakeSession:
    def __init__(self):
        self.page = FakePage()
        self.wheel_dx = 0.0
        self.wheel_dy = 0.0
        self.wheel_at = (10, 10)
        self.wheel_task = None


def test_debt_is_paid_in_steps_no_larger_than_one():
    async def run():
        engine = BrowserEngine.__new__(BrowserEngine)
        s = FakeSession()
        s.wheel_dy = 120.0
        await engine._drain_wheel(s)
        return s.page.mouse.wheels

    wheels = asyncio.run(run())
    assert len(wheels) >= 3, "a notch should arrive as several steps"
    assert all(abs(dy) <= WHEEL_STEP_PX + 0.01 for _, dy in wheels)
    assert abs(sum(dy for _, dy in wheels) - 120.0) < 0.01, "all of it lands"


def test_a_small_delta_lands_in_one_step():
    async def run():
        engine = BrowserEngine.__new__(BrowserEngine)
        s = FakeSession()
        s.wheel_dy = 12.0  # a trackpad-sized nudge
        await engine._drain_wheel(s)
        return s.page.mouse.wheels

    assert asyncio.run(run()) == [(0.0, 12.0)]


def test_reversing_cancels_what_is_still_owed():
    """The reported symptom: flick up while a down-scroll is still paying off."""
    async def run():
        engine = BrowserEngine.__new__(BrowserEngine)
        s = FakeSession()
        s.wheel_dy = 600.0  # a long way still owed
        task = asyncio.ensure_future(engine._drain_wheel(s))
        await asyncio.sleep(0.03)          # a few steps go out
        s.wheel_dy = -80.0                 # the user flicks the other way
        await task
        return s.page.mouse.wheels

    wheels = asyncio.run(run())
    down = sum(dy for _, dy in wheels if dy > 0)
    up = sum(dy for _, dy in wheels if dy < 0)
    assert up < 0, "the reversal actually moved the page back"
    assert down < 600, "the rest of the old scroll was abandoned, not replayed"


def test_debt_is_bounded():
    assert _clamp_debt(10 * WHEEL_MAX_DEBT_PX) == WHEEL_MAX_DEBT_PX
    assert _clamp_debt(-10 * WHEEL_MAX_DEBT_PX) == -WHEEL_MAX_DEBT_PX
