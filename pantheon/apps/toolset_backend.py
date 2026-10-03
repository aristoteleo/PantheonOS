"""Expose a ToolSet through an ordinary AppContext without a global RPC bus.

The caller supplies the service and owns constructor policy. Only its declared
tool methods are registered; the App/gateway still owns caller authorization.
"""
import asyncio
import inspect
from functools import wraps


async def register_toolset(ctx, service):
    """Transfer lifecycle ownership to the portable host, including setup failure.

    ToolSet methods already own their concurrency. Keeping them concurrent lets
    interrupt/status work while a chat or kernel call is in progress. A request
    timeout does not cancel or replay an admitted mutation.
    """
    ctx.require_rpc_token = True
    active = set()
    accepting = True
    shutdown = cleanup = None

    async def stop():
        nonlocal accepting, shutdown
        accepting = False
        if shutdown is None:
            shutdown = asyncio.create_task(service.begin_shutdown())
        await asyncio.shield(shutdown)

    async def dispose():
        try:
            await stop()
        finally:
            while active:
                await asyncio.gather(*tuple(active), return_exceptions=True)
            await service.cleanup()

    async def close():
        nonlocal cleanup
        if cleanup is None:
            cleanup = asyncio.create_task(dispose())
        await asyncio.shield(cleanup)

    async def before_stop():
        await stop()
        if active:
            raise RuntimeError('App calls are still draining')
        # A successful pre-stop includes durable saves, dependency shutdown and
        # any background work the ToolSet owns outside an HTTP request.
        await close()

    ctx.on_cleanup(close)
    ctx.before_stop = before_stop
    try:
        methods = {}
        for name, (method, _) in service.functions.items():
            if name in ctx._methods:
                raise ValueError('App method conflicts with its ToolSet')
            signature = inspect.signature(method)
            allowed = {name for name, p in signature.parameters.items()
                       if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)} - {'context_variables', 'ctx', 'context'}

            accepts_extra = any(p.kind == p.VAR_KEYWORD for p in signature.parameters.values())

            def wrap(fn, parameters, extra):
                @wraps(fn)
                async def invoke(**args):
                    if not accepting:
                        raise RuntimeError('App is stopping; new calls are not accepted')
                    framework = {'context_variables', 'ctx', 'context', 'session_id'} - parameters
                    if args.keys() & framework or not extra and args.keys() - parameters:
                        raise ValueError('Unknown or framework-only RPC arguments')
                    task = asyncio.current_task()
                    active.add(task)
                    try:
                        # Do not inherit an in-process Agent's callback or cwd.
                        from pantheon.toolset import ExecutionContext, set_current_context_variables, reset_current_context_variables
                        token = set_current_context_variables(ExecutionContext())
                        try:
                            return await fn(**args, context_variables={})
                        finally:
                            reset_current_context_variables(token)
                    finally:
                        active.remove(task)
                return invoke
            methods[name] = wrap(method, allowed, accepts_extra)
        await service.run(remote=False, cleanup_on_exit=False)
    except BaseException:
        await close()
        raise
    ctx._methods.update(methods)
    ctx.concurrent_methods.update(methods)
