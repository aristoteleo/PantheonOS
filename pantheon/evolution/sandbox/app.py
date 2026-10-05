"""Ordinary isolated mutation App entry; code inputs arrive through its API.

This App must be placed inside the caller's isolation boundary. It does not
construct an Agent, select model providers or fetch controller credentials.
"""
import asyncio

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.python import PythonInterpreterToolSet
from pantheon.settings import Settings
from ..local_shell import LocalShellToolSet
from ..lifetime import join_cleanup
from .tool_backend import register_sandbox_mutation


async def register(ctx):
    child = AppContext(ctx.app_id, ctx.workspace, ctx.state_dir, ctx._rpc)
    initializing = closing = None
    stopping = False
    ctx.require_rpc_token = True

    async def stop():
        nonlocal stopping, closing
        stopping = True
        async def dispose():
            if initializing is not None:
                await asyncio.gather(initializing, return_exceptions=True)
            if child._cleanup is not None:
                await child._cleanup()
        if closing is None:
            closing = asyncio.create_task(dispose())
        await join_cleanup(closing)
    ctx.begin_shutdown = stop
    ctx.on_cleanup(stop)

    @ctx.method
    async def initialize(parent_files: dict, evaluator_code: str, objective: str,
                         timeout: int = 600, inspirations: list | None = None):
        nonlocal initializing
        if stopping or initializing is not None:
            raise RuntimeError('This mutation App is single-use; reconcile its existing initialization')
        async def tools(work):
            settings = Settings(ctx.state_dir / 'settings', isolated_env=True,
                                environment={}, user_home=ctx.state_dir / 'home')
            return {'files': FileManagerToolSet('files', str(work), file_settings=settings, template_fallback=False),
                    'shell': LocalShellToolSet('shell', str(work)),
                    'python': PythonInterpreterToolSet('python', str(work), strict_lifecycle=True,
                                                       execution_timeout=timeout)}
        initializing = asyncio.create_task(register_sandbox_mutation(child, tool_factory=tools,
            parent_files=parent_files, evaluator_code=evaluator_code, objective=objective,
            timeout=timeout, inspirations=inspirations or []))
        initializing.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        await asyncio.shield(initializing)
        return {'initialized': True}

    def backend():
        if stopping or initializing is None or not initializing.done():
            raise RuntimeError('The mutation App is not initialized or is stopping')
        return initializing.result()

    @ctx.method
    def describe():
        return backend().describe()

    @ctx.method
    async def invoke_tool(provider: str, name: str, args: dict):
        return await backend().invoke_tool(provider, name, args)

    @ctx.method
    async def evaluate_initial():
        return await backend().evaluate_initial()

    @ctx.method
    async def finish(error: str = ''):
        return await backend().finish(error)

    ctx.concurrent_methods.update(('initialize', 'describe', 'invoke_tool', 'evaluate_initial', 'finish'))
