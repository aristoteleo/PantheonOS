"""Explicit control-plane credentials for a prepared Modal App placement.

One private node-vault credential stores JSON {token_id, token_secret}. The
endpoint is fixed to Modal's control plane. No environment or global client is
consulted, and this credential is never forwarded to a workload container.
"""
import asyncio
import json

from .modal_sandbox import _join


class ModalCredentialOwner:
    def __init__(self, credential):
        try:
            data = json.loads(credential.key)
            if (credential.endpoint != 'https://api.modal.com'
                    or not isinstance(data, dict) or set(data) != {'token_id', 'token_secret'}
                    or not all(isinstance(v, str) and 1 <= len(v) <= 4096 and not any(c.isspace() for c in v)
                               for v in data.values())):
                raise ValueError
        except (AttributeError, ValueError, TypeError):
            raise ValueError('Invalid prepared Modal control-plane credential') from None
        self._credentials = data['token_id'], data['token_secret']
        self.client = self.opening = self.closing = None
        self.closed = False

    async def start(self):
        if self.closed:
            raise RuntimeError('Modal control-plane client is closing')
        async def open_client():
            import modal
            from modal_proto import api_pb2
            self.client = modal.Client('https://api.modal.com', api_pb2.CLIENT_TYPE_CLIENT, self._credentials)
            await self.client.__aenter__()
            return self.client
        if self.opening is None:
            self.opening = asyncio.create_task(open_client())
            self.opening.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(self.opening)

    async def close(self):
        self.closed = True
        async def dispose():
            if self.opening is not None:
                await asyncio.gather(self.opening, return_exceptions=True)
            if self.client is not None:
                await self.client.__aexit__(None, None, None)
            self.client = self.opening = None
            self._credentials = ()
        if self.closing is None:
            self.closing = asyncio.create_task(dispose())
        await _join(self.closing)
