"""MCP sampling through the existing scoped Model Services client.

No Agent, model discovery fallback, provider keys or autonomous tool execution.
An owner chooses the exact model/route and bounded sampling budget. MCP servers
may request samples only while an exported tool call is executing.
"""
import asyncio
import base64
from contextlib import contextmanager
import json
import math

from pantheon.apps.model_sampling import ModelBinding


class ModelSampling(ModelBinding):
    @contextmanager
    def admit(self, server):
        if self._closed:
            raise RuntimeError('MCP sampling is stopping')
        lease = {'remaining': self.requests}
        self._leases.setdefault(server, []).append(lease)
        try:
            yield
        finally:
            # Equal-valued leases must not remove another concurrent call's
            # budget. The MCP callback protocol has no trusted parent-call ID.
            leases = self._leases[server]
            leases[:] = [item for item in leases if item is not lease]
            if not leases:
                del self._leases[server]

    def _request(self, messages, params):
        try:
            if (getattr(params, 'includeContext', None) not in (None, 'none')
                    or getattr(params, 'tools', None) or getattr(params, 'toolChoice', None)
                    or not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 128
                    or type(params.maxTokens) is not int or params.maxTokens <= 0):
                raise ValueError
            out = []
            system = getattr(params, 'systemPrompt', None)
            if system is not None:
                if not isinstance(system, str):
                    raise ValueError
                out.append({'role': 'system', 'content': system})
            for message in messages:
                if message.role not in ('user', 'assistant'):
                    raise ValueError
                content = message.content if isinstance(message.content, list) else [message.content]
                if not content or len(content) > 64:
                    raise ValueError
                blocks = []
                for block in content:
                    if block.type == 'text' and isinstance(block.text, str):
                        blocks.append({'type': 'text', 'text': block.text})
                    elif block.type == 'image' and block.mimeType in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
                        # Decode only after enforcing the request size. Never
                        # fetch a URI or inherit files/roots from another App.
                        if not isinstance(block.data, str) or len(block.data) > 512 * 1024:
                            raise ValueError
                        base64.b64decode(block.data, validate=True)
                        blocks.append({'type': 'image_url', 'image_url': {
                            'url': f'data:{block.mimeType};base64,{block.data}'}})
                    else:
                        raise ValueError
                value = '\n'.join(b['text'] for b in blocks) if all(b['type'] == 'text' for b in blocks) else blocks
                out.append({'role': message.role, 'content': value})
            options = {'max_tokens': min(params.maxTokens, self.max_tokens)}
            temperature = getattr(params, 'temperature', None)
            if temperature is not None:
                if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
                    raise ValueError
                options['temperature'] = temperature
            stops = getattr(params, 'stopSequences', None)
            if stops is not None:
                if (not isinstance(stops, list) or len(stops) > 16
                        or not all(isinstance(s, str) and len(s) <= 1024 for s in stops)):
                    raise ValueError
                options['stop'] = stops
            if len(json.dumps([out, options], allow_nan=False).encode()) > 512 * 1024:
                raise ValueError
            return out, options
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise ValueError('MCP sampling request is unsupported or exceeds its limits') from None

    async def sample(self, server, messages, params, context=None):
        if self._closed:
            raise RuntimeError('MCP sampling is stopping')
        converted, options = self._request(messages, params)
        lease = next((item for item in self._leases.get(server, []) if item['remaining'] > 0), None)
        if lease is None or len(self._pending) >= 8:
            raise ValueError('MCP sampling requires an active tool call with remaining budget')
        lease['remaining'] -= 1
        task = asyncio.create_task(self.client.complete(self.model, messages=converted, model_params=options))
        self._pending.add(task)
        try:
            # Cancellation uses Model Services' original cancel-before-disconnect
            # path. It never repeats a paid or stateful inference request.
            result = await task
            if (not isinstance(result, dict) or not isinstance(result.get('content'), str)
                    or result.get('tool_calls') or len(result['content'].encode()) > 512 * 1024):
                raise ValueError
            from mcp.types import CreateMessageResult, TextContent
            return CreateMessageResult(role='assistant', model=result.get('model') or self.model,
                content=TextContent(type='text', text=result['content']),
                stopReason='maxTokens' if result.get('finish_reason') == 'length' else 'endTurn')
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RuntimeError('MCP sampling through Model Services failed; it was not retried') from None
        finally:
            self._pending.discard(task)
