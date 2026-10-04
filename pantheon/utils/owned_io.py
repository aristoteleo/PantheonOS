"""Drain admitted local I/O before a cancelled App request releases its owner."""
import asyncio


async def run_owned_io(function, *args, **kwargs):
    """Cancellation is not proof that a synchronous disk operation stopped.

    Keep the request alive until its worker completes, even after repeated
    cancellation, so host drain cannot release the App's data lock too early.
    Callers must still treat cancellation as an unknown result and reread state.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result
