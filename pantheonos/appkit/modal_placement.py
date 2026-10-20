"""Owned ordinary App process on a prepared Modal image.

The same placement object can serve any stdio App. It contains no Agent or
Evolution behavior. Create it synchronously and register close before start.
"""
import asyncio

from .modal_app_transport import ModalAppTransport
from .modal_sandbox import ModalSandboxOwner, _join, validate_modal_request


class ModalAppPlacement:
    def __init__(self, owner):
        self.owner = owner
        self.pipe = self.task = self.closing = None
        self.backend_id = None
        self.closed = False

    async def start(self):
        if self.closed:
            raise RuntimeError('App placement is closing')
        async def launch():
            sandbox = await self.owner.start()
            self.backend_id = sandbox.object_id
            self.pipe = ModalAppTransport(sandbox)
            await self.pipe.ready()
            return self
        if self.task is None:
            self.task = asyncio.create_task(launch())
            self.task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(self.task)

    async def invoke(self, method, args):
        if self.closed or self.pipe is None:
            raise RuntimeError('App placement is unavailable')
        return await self.pipe.invoke(method, args)

    async def close(self):
        self.closed = True
        async def dispose():
            if self.task is not None and not self.task.done():
                self.task.cancel()
            receipt = await self.owner.stop()
            if self.pipe is not None:
                await self.pipe.disconnect()
            if self.task is not None:
                await asyncio.gather(self.task, return_exceptions=True)
            return receipt
        if self.closing is None:
            self.closing = asyncio.create_task(dispose())
        return await _join(self.closing)


class PreparedModalApp:
    """Explicit image/resource policy usable as a generic placement factory."""
    def __init__(self, image, *, app_name, timeout=900, cpu=1, memory=2048, gpu=None, modal_client=None):
        validate_modal_request(app_name, image.image_id, image.argv(), {}, timeout, cpu, memory, gpu)
        self.image, self.app_name = image, app_name
        self.modal_client = modal_client
        self.timeout, self.cpu, self.memory, self.gpu = timeout, cpu, memory, gpu

    def __call__(self, root, *, operation_id):
        return ModalAppPlacement(ModalSandboxOwner(root, operation_id=operation_id,
            app_name=self.app_name, image_id=self.image.image_id, argv=self.image.argv(),
            timeout=self.timeout, cpu=self.cpu, memory=self.memory, gpu=self.gpu, modal_client=self.modal_client))
