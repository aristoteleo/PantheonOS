import asyncio
import threading

import pytest

from pantheon.utils.owned_io import run_owned_io


@pytest.mark.asyncio
@pytest.mark.parametrize('fails', [False, True])
async def test_repeated_cancellation_drains_worker_before_request_finishes(fails):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    def write():
        entered.set()
        assert release.wait(5)
        completed.set()
        if fails:
            raise OSError('write failed')
        return 'saved'
    task = asyncio.create_task(run_owned_io(write))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_result_and_failures_are_preserved_without_cancellation():
    assert await run_owned_io(lambda a, b: a + b, 2, b=3) == 5
    def failure():
        raise OSError('disk failure')
    with pytest.raises(OSError, match='disk failure'):
        await run_owned_io(failure)
