"""Process lifetime for ToolSet Apps, independent of Agent and platform bootstrap.

The host owns setup failure and exactly one cleanup. A stop request ends service
registration, drains accepted RPCs, then disposes App resources and transports.
Only the CLI installs signal handlers; embedded users supply a stop event.
"""

import asyncio


class AppShutdownError(RuntimeError):
    """Keep all shutdown failures without requiring Python 3.11 groups."""

    def __init__(self, errors):
        super().__init__("App shutdown did not finish cleanly")
        self.errors = tuple(errors)


async def _call(target, method):
    # Resolve inside the task so a broken/partial transport cannot prevent
    # cleanup of the other transport and the App itself.
    await getattr(target, method)()


async def _shutdown(service):
    workers = [getattr(service, name, None)
               for name in ("worker", "_frontend_worker")]
    backends = [getattr(service, name, None)
                for name in ("_backend", "_frontend_backend")]
    errors = []
    # Close admission on every channel before calling the App's stop policy.
    # An App can cancel its own observers/requests; default policy lets accepted
    # calls finish. Neither path silently cancels a synchronous mutation thread.
    results = await asyncio.gather(
        *(_call(worker, "stop") for worker in workers if worker is not None),
        return_exceptions=True,
    )
    errors.extend(result for result in results if isinstance(result, BaseException))
    try:
        await service.begin_shutdown()
    except BaseException as exc:
        errors.append(exc)
    results = await asyncio.gather(
        *(_call(worker, "drain") for worker in workers if worker is not None),
        return_exceptions=True,
    )
    errors.extend(result for result in results if isinstance(result, BaseException))
    try:
        await service.cleanup()
    except BaseException as exc:
        errors.append(exc)
    finally:
        # These transports are created by this service's run(), never borrowed
        # from the desktop, platform or another App.
        for backend in backends:
            if backend is not None:
                try:
                    await backend.close()
                except BaseException as exc:
                    errors.append(exc)
    if errors:
        raise AppShutdownError(errors) from errors[0]


async def serve_toolset(service, *, remote=True, stop=None):
    """Run one App until worker exit or stop; never restart/replay it implicitly.

    Accepted RPCs, including synchronous operations already running in a thread,
    must finish before cleanup. The Fleet supervisor owns the hard stop deadline;
    this function never labels a forced/unfinished stop as clean.
    """
    stop = stop if stop is not None else asyncio.Event()
    runner = waiter = None
    failure = None
    try:
        if not stop.is_set():
            runner = asyncio.create_task(service.run(remote=remote, cleanup_on_exit=False))
            waiter = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait((runner, waiter), return_when=asyncio.FIRST_COMPLETED)
            if runner in done:
                await runner
    except BaseException as exc:
        failure = exc
    finally:
        async def finish():
            nonlocal failure
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            if runner is not None:
                if not runner.done():
                    runner.cancel()
                result, = await asyncio.gather(runner, return_exceptions=True)
                if failure is None and isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    failure = result
            await _shutdown(service)

        cleanup = asyncio.create_task(finish())
        # An outer cancellation must not let asyncio.run cancel cleanup or its
        # accepted mutations. Repeated OS stop signals only set the same event.
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                if failure is None:
                    failure = exc
            except BaseException:
                break
        try:
            cleanup.result()
        except BaseException as exc:
            if failure is not None:
                raise AppShutdownError([failure, exc]) from failure
            raise
    if failure is not None:
        raise failure
