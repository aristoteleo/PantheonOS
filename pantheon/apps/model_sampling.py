"""Bounded App-owned inference through the original Model Services client.

This SDK has no Agent implementation, ambient providers or parent conversation.
Credentials and the exact model are chosen by prepared deployment configuration.
"""
import asyncio
import base64
from collections.abc import Mapping
from contextlib import contextmanager
import json
import os
from pathlib import Path

from pantheon.apps.dependency_client import DependencyClient
from pantheon.models.client import parse_ref, parse_route_ref
from pantheon.models.dependency import DependencyModelServices


def model_client(credential, *, client_factory=None):
    """Construct the canonical consumer client, never an ambient provider SDK."""
    dependency = DependencyClient(credential)
    if client_factory is not None:
        return client_factory(credential)
    import pantheon.models.client as module
    binary = Path(module.__file__).with_name('fleet-app-transport.exe' if os.name == 'nt' else 'fleet-app-transport')
    return DependencyModelServices(dependency, direct_executable=str(binary) if binary.is_file() else '')


class ModelClientOwner:
    def __init__(self, credential, *, client_factory=None):
        self.client = model_client(credential, client_factory=client_factory)
        self._leases = {}
        self._pending = set()
        self._closed = False
        self._closing = None

    async def close(self):
        self._closed = True
        if self._closing is None:
            async def finish():
                if self._pending:
                    await asyncio.gather(*tuple(self._pending), return_exceptions=True)
                await self.client.aclose()
            self._closing = asyncio.create_task(finish())
        # A cancelled cleanup caller must not abandon the shared HTTP pools.
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not self._closing.cancelled():
                self._closing.exception()
            raise asyncio.CancelledError
        return self._closing.result()


class ModelBinding(ModelClientOwner):
    def __init__(self, spec, credentials, *, client_factory=None):
        try:
            if (not isinstance(spec, Mapping) or set(spec) != {'credential', 'model', 'max_tokens', 'max_requests_per_call'}
                    or not isinstance(spec['credential'], str) or spec['credential'] not in credentials
                    or not isinstance(spec['model'], str)
                    or type(spec['max_tokens']) is not int or not 1 <= spec['max_tokens'] <= 32768
                    or type(spec['max_requests_per_call']) is not int or not 1 <= spec['max_requests_per_call'] <= 16):
                raise ValueError
            ref = spec['model']
            (parse_route_ref if ref.startswith('fleet-route://') else parse_ref)(ref)
            # Validate even injected test/application factories against the same
            # dependency credential shape, before creating any pooled clients.
            credential = credentials[spec['credential']]
            DependencyClient(credential)
        except (TypeError, ValueError, KeyError, AttributeError):
            raise ValueError('Invalid Model Services sampling binding') from None
        self.model, self.max_tokens, self.requests = ref, spec['max_tokens'], spec['max_requests_per_call']
        super().__init__(credential, client_factory=client_factory)


class ToolModelSampling(ModelBinding):
    """A fresh sampling budget and revocable callback for each admitted RPC.

    ToolContext.call_agent remains the compatibility API, but this callback
    performs one inference through an ordinary dependency, not an Agent run.
    """
    @contextmanager
    def context(self):
        if self._closed:
            raise RuntimeError('Model sampling is stopping')
        remaining = self.requests
        active = True

        async def call(*, messages, system_prompt=None, model=None, use_memory=False):
            nonlocal remaining
            if not active or self._closed or remaining <= 0 or len(self._pending) >= 8:
                return {'success': False, 'error': 'Model sampling requires an active tool call with remaining budget'}
            try:
                # Caller preferences cannot substitute a different deployment.
                if model not in (None, self.model):
                    raise ValueError
                converted = self._messages(messages, system_prompt)
            except (ValueError, TypeError, RecursionError):
                return {'success': False, 'error': 'Sampling request is unsupported or exceeds the configured model limits'}
            remaining -= 1
            task = asyncio.create_task(self.client.complete(self.model, messages=converted,
                                                            model_params={'max_tokens': self.max_tokens}))
            self._pending.add(task)
            try:
                result = await task
                if (not isinstance(result, dict) or not isinstance(result.get('content'), str)
                        or result.get('tool_calls') or len(result['content'].encode()) > 512 * 1024):
                    raise ValueError
                return {'success': True, 'response': result['content'], 'model': result.get('model') or self.model,
                        'usage': result.get('usage') or {},
                        '_metadata': {**(result.get('_metadata') or {}), 'sampling': {
                            'execution': 'model_services', 'memory_requested': bool(use_memory), 'memory_used': False}}}
            except asyncio.CancelledError:
                raise
            except Exception:
                return {'success': False, 'error': 'Sampling through Model Services failed; it was not retried'}
            finally:
                self._pending.discard(task)

        try:
            yield {'_call_agent': call}
        finally:
            active = False

    @staticmethod
    def _messages(messages, system):
        # Copy before awaiting: no subsequent tool mutation can change the
        # admitted request. Neither arbitrary URLs nor provider options cross.
        raw = json.dumps([messages, system], allow_nan=False)
        if len(raw.encode()) > 16 * 1024 * 1024:
            raise ValueError
        messages, system = json.loads(raw)
        if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
            raise ValueError
        out = []
        if system is not None:
            if not isinstance(system, str):
                raise ValueError
            out.append({'role': 'system', 'content': system})
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                    or message['role'] not in ('user', 'assistant', 'system')):
                raise ValueError
            content = message['content']
            if not isinstance(content, str):
                if not isinstance(content, list) or not 1 <= len(content) <= 64:
                    raise ValueError
                for block in content:
                    if not isinstance(block, dict):
                        raise ValueError
                    if set(block) == {'type', 'text'} and block['type'] == 'text' and isinstance(block['text'], str):
                        continue
                    if set(block) != {'type', 'image_url'} or block['type'] != 'image_url':
                        raise ValueError
                    image = block['image_url']
                    if not isinstance(image, dict) or set(image) != {'url'} or not isinstance(image['url'], str):
                        raise ValueError
                    header, payload = image['url'].split(',', 1)
                    if header not in {f'data:image/{kind};base64' for kind in ('png', 'jpeg', 'webp', 'gif')}:
                        raise ValueError
                    if not payload:
                        raise ValueError
                    base64.b64decode(payload, validate=True)
            out.append(message)
        return out
