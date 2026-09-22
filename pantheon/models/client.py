"""Exact-node model invocation over Fleet's HTTP data plane, never NATS token RPCs."""
import asyncio
import json
import os
import re
import time
import uuid
import hashlib
from collections import OrderedDict
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote, urlsplit

import httpx
from loguru import logger
from .routing import parse_route_ref, location, summary, select
from .direct import DirectHTTPTransport, DirectUnavailable, ORIGIN, binary as direct_binary
from .direct_session import PeerPool


class ControlError(RuntimeError):
    def __init__(self, status, detail=None):
        self.status = status
        super().__init__(detail if isinstance(detail, str) and len(detail) <= 500 else
                         f'Model Services control plane returned HTTP {status}; refresh or update Hub/Fleet')


def model_ref(deployment_id, model):
    return f'fleet-model://{deployment_id}/{quote(model, safe="")}'


def parse_ref(ref):
    parts = urlsplit(ref)
    if (parts.scheme != 'fleet-model' or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', parts.netloc)
            or parts.query or parts.fragment or not parts.path.startswith('/') or len(parts.path) < 2):
        raise ValueError('Invalid Fleet model reference')
    return parts.netloc, unquote(parts.path[1:])


async def stream_events(response):
    """Bound SSE buffering, including a malicious/accidental unterminated line."""
    pending, event, size = bytearray(), [], 0
    # Leave chunk_size unset: filling a 16 KiB buffer would delay the first
    # token of ordinary short generations until the upstream response ends.
    async for block in response.aiter_bytes():
        size += len(block)
        if size > 8 * 1024 * 1024:
            raise ValueError('Model output exceeds 8 MiB')
        pending.extend(block)
        while b'\n' in pending:
            line, _, rest = pending.partition(b'\n')
            pending = bytearray(rest)
            line = line.rstrip(b'\r')
            if line.startswith(b'data:'):
                event.append(line[5:].lstrip(b' '))
                if sum(map(len, event)) > 1024 * 1024:
                    raise ValueError('Model stream event exceeds 1 MiB')
            elif not line and event:
                yield b'\n'.join(event).decode('utf-8')
                event = []
        if len(pending) + sum(map(len, event)) > 1024 * 1024:
            raise ValueError('Model stream event exceeds 1 MiB')
    # A final partial event is not a valid completion marker.


@asynccontextmanager
async def cancel_before_disconnect(stream, cancel):
    async with stream as response:
        try:
            yield response
        except BaseException:
            # Closing SSE first can win the race to the node, recording an
            # unknown connection loss before the explicit cancellation arrives.
            await asyncio.shield(cancel())
            raise


class ModelServices:
    def __init__(self, hub=None, token=None, transport=None, *, direct_executable=None, prefer_direct=False):
        self.hub = (hub or os.getenv('PANTHEON_HUB_URL', '')).rstrip('/')
        self.token = token
        self.transport = transport
        self.grants = {}
        self.metadata = {}
        self.direct_executable = direct_executable if direct_executable is not None else (direct_binary() if transport is None else None)
        self.direct_unavailable = {}
        # Direct-only aliases opt in immediately. Keep ordinary calls on their
        # existing path until real transport benchmarks justify a default change.
        self.prefer_direct = prefer_direct
        # Bound helper processes during parallel route probes/model calls. Each
        # invocation reserves room for its own cancellation connection, so full
        # inference admission cannot deadlock cancellation behind that same cap.
        self.direct_limit = asyncio.Semaphore(8)
        self.direct_peers = PeerPool(self.direct_executable)

    async def aclose(self):
        await self.direct_peers.aclose()

    def headers(self):
        token = self.token or os.getenv('FLEET_KEY', '')
        if not self.hub or not token:
            raise RuntimeError('Connect this runtime to Hub and Fleet to use Model Services')
        return {'Authorization': 'Bearer ' + token}

    async def hub_request(self, method, path, data=None):
        async with httpx.AsyncClient(timeout=25, transport=self.transport, follow_redirects=False) as client:
            result = await client.request(method, self.hub + path, headers=self.headers(), json=data)
        if result.status_code >= 400:
            try:
                detail = result.json().get('detail')
            except (ValueError, AttributeError):
                detail = None
            raise ControlError(result.status_code, detail)
        return result.json()

    async def deployments(self):
        rows = (await self.hub_request('GET', '/api/model-services'))['deployments']
        # Directory refreshes also run during unrelated exact-model calls.
        # Keep alias metadata until the route directory itself is refreshed.
        self.metadata = {**{ref: spec for ref, spec in self.metadata.items()
                            if ref.startswith('fleet-route://')},
                         **{model_ref(row['deployment_id'], model['id']): model
                            for row in rows for model in row['models']}}
        return rows

    async def deployment(self, deployment_id):
        row = next((d for d in await self.deployments() if d['deployment_id'] == deployment_id), None)
        if not row:
            raise ValueError('This model service is not in your Fleet directory')
        return row

    async def save(self, row):
        return await self.hub_request('PUT', '/api/model-services/' + row['deployment_id'], row)

    async def routes(self):
        try:
            routes = (await self.hub_request('GET', '/api/model-services/routes'))['routes']
            valid = {'fleet-route://' + route['route_id'] for route in routes}
            self.metadata = {ref: spec for ref, spec in self.metadata.items()
                             if not ref.startswith('fleet-route://') or ref in valid}
            return routes
        except ControlError as error:
            if error.status == 404:
                return []  # Existing exact-model consumers work with an older Hub.
            raise

    async def route_operation(self, action='list', route=None, route_id='', revision=0, requires=None):
        if action == 'list':
            return {'routes': await self.routes()}
        if action == 'save':
            route_id = (route or {}).get('route_id', '')
        parse_route_ref('fleet-route://' + route_id)
        path = '/api/model-services/routes/' + route_id
        if action == 'save':
            return await self.hub_request('PUT', path, route)
        if action == 'delete':
            return await self.hub_request('DELETE', path, {'revision': revision})
        if action == 'resolve':
            return await self.hub_request('POST', path + '/resolve', requires or {})
        raise ValueError('Unsupported route operation')

    async def describe(self, ref):
        if ref.startswith('fleet-route://'):
            route_id = parse_route_ref(ref)
            rows = {r['deployment_id']: r for r in await self.deployments()}
            route = next((r for r in await self.routes() if r['route_id'] == route_id), None)
            if not route:
                raise ValueError('Route is not in your model directory')
            spec = summary(route, rows)
            self.metadata[ref] = spec
            return {'name': route['name'], 'engine': 'Fleet route', 'node_id': '', 'node_name': 'Explicit routing policy'}, spec
        deployment_id, native_id = parse_ref(ref)
        row = await self.deployment(deployment_id)
        spec = next((m for m in row['models'] if m['id'] == native_id), None)
        return row, spec

    @staticmethod
    def source(row):
        node = row.get('node_name') or row['node_id']
        return dict(id='fleet:' + row['deployment_id'], label=f"{row['name']} · {node}",
                    billing='Your API account' if row['engine'] == 'api' else 'Local compute' if row.get('mode') == 'managed' else 'Unconfirmed · attached engine',
                    endpoint=f"Fleet node: {node}", available=row['state'] == 'ready',
                    reason='Start the model service on its selected node.', node_id=row['node_id'],
                    compute='External API' if row['engine'] == 'api' else 'Engine on ' + node if row.get('mode') == 'managed' else 'Unconfirmed · attached engine',
                    egress_node=node)

    async def catalog(self):
        rows = await self.deployments()
        sources, models = [], []
        for row in rows:
            source = self.source(row)
            sources.append(source)
            for m in row['models']:
                models.append(dict(model=model_ref(row['deployment_id'], m['id']),
                    name=m.get('name') or m['id'], vendor=row['engine'], source=source['id'],
                    operations=m['operations'], capabilities={k: m.get(k) for k in
                        ('tools', 'vision', 'reasoning', 'structured_output')}, context=m.get('context'),
                    metadata_source='Fleet service · user-confirmed capabilities',
                    description=f"Service: {row['name']} · Node: {row.get('node_name') or row['node_id']}"))
        by_id = {r['deployment_id']: r for r in rows}
        for route in await self.routes():
            spec = summary(route, by_id)
            ref, source_id = 'fleet-route://' + route['route_id'], 'fleet-route:' + route['route_id']
            self.metadata[ref] = spec
            sources.append({'id': source_id, 'label': route['name'] + ' · Route',
                'billing': 'Explicit route policy', 'endpoint': ref,
                'available': route['transport'] == 'relay_allowed' or bool(self.direct_executable),
                'reason': 'This route requires an updated Fleet direct helper and a reachable authorized node.'})
            models.append({'model': ref, 'name': route['name'], 'vendor': 'Fleet route', 'source': source_id,
                'operations': spec['operations'], 'context': spec['context'],
                'capabilities': {k: spec.get(k) for k in ('tools', 'vision', 'reasoning', 'structured_output')},
                'metadata_source': 'Explicit Fleet route policy',
                'description': f"{len(route['candidates'])} authorized candidates · {route['fallback']} fallback before submission"})
        return sources, models

    async def connect(self, row):
        if row['state'] != 'ready' or not row.get('binding'):
            raise RuntimeError('This model service is stopped or not ready; open Model Services')
        key = json.dumps(row['binding'], sort_keys=True)
        self.grants = {k: v for k, v in self.grants.items() if v.get('expires', 0) > time.time() + 30}
        if key in self.grants:
            return self.grants[key]
        grant = await self.hub_request('POST', '/api/fleet/apps/workload-connect', row['binding'])
        origin = urlsplit(grant.get('origin', ''))
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment or not grant.get('access_token')):
            raise RuntimeError('Invalid Fleet model transport grant')
        if len(self.grants) >= 64:
            self.grants.pop(next(iter(self.grants)))
        self.grants[key] = grant
        return grant

    @asynccontextmanager
    async def connection(self, row, policy='relay_allowed', grant=None):
        """Choose transport before HTTP submission, then freeze it for this use."""
        if policy not in ('relay_allowed', 'direct_only'):
            raise ValueError('Unsupported model transport policy')
        if row['state'] != 'ready' or not row.get('binding'):
            raise RuntimeError('This model service is stopped or not ready; open Model Services')
        key = json.dumps(row['binding'], sort_keys=True)
        self.direct_unavailable = {k: until for k, until in self.direct_unavailable.items() if until > time.monotonic()}

        async def issue(peer):
            try:
                result = await self.hub_request('POST', '/api/fleet/apps/workload-direct-connect',
                                                {**row['binding'], 'peer_id': peer})
            except ControlError as error:
                if error.status in (404, 405, 501, 503):
                    raise DirectUnavailable('Direct App protocol unavailable on this node') from error
                raise  # Ownership, stale binding and malformed grants never trigger fallback.
            except httpx.RequestError as error:
                raise DirectUnavailable('Direct control connection unavailable') from error
            if any(result.get('binding', {}).get(k) != v for k, v in row['binding'].items()):
                raise ValueError('Direct App grant does not match the selected model instance')
            return result

        if self.direct_executable and (policy == 'direct_only' or (self.prefer_direct and key not in self.direct_unavailable)):
            direct = DirectHTTPTransport(self.direct_executable, issue, limit=self.direct_limit,
                                         peers=self.direct_peers, node=row['node_id'])
            try:
                async with asyncio.timeout(10):
                    await direct.prepare()
            except (DirectUnavailable, TimeoutError):
                await direct.aclose()
                if policy == 'direct_only':
                    raise DirectUnavailable('Authenticated direct transport is unavailable. Nothing was sent to Relay.') from None
                if len(self.direct_unavailable) >= 64:
                    self.direct_unavailable.pop(next(iter(self.direct_unavailable)))
                self.direct_unavailable[key] = time.monotonic() + 30
            except BaseException:
                await direct.aclose()
                raise
            else:
                async with httpx.AsyncClient(transport=direct, timeout=httpx.Timeout(120, connect=20), follow_redirects=False) as client:
                    yield client, {'origin': ORIGIN, 'access_token': '', '_transport': 'fleet_direct'}, 'fleet_direct'
                return
        if policy == 'direct_only':
            raise DirectUnavailable('Authenticated direct transport is unavailable. Nothing was sent to Relay.')
        if not grant or grant.get('_transport') == 'fleet_direct':
            grant = await self.connect(row)
        async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(120, connect=20), follow_redirects=False) as client:
            yield client, {**grant, '_transport': 'fleet_relay'}, 'fleet_relay'

    async def complete(self, ref, messages=None, tools=None, response_format=None,
                       model_params=None, process_chunk=None, operation='text', inputs=None, required_context=0):
        route_started = time.monotonic()
        grant, routing = None, {}
        vision = any(isinstance(m.get('content'), list) and any(p.get('type') in ('image_url', 'image')
                     for p in m['content'] if isinstance(p, dict)) for m in messages or [])
        if ref.startswith('fleet-route://'):
            row, spec, grant, routing = await select(self, ref, {'operation': operation, 'tools': bool(tools),
                'vision': vision, 'structured_output': bool(response_format), 'context': required_context})
            deployment_id, model = row['deployment_id'], spec['id']
        else:
            deployment_id, model = parse_ref(ref)
            row = await self.deployment(deployment_id)
            spec = next((m for m in row['models'] if m['id'] == model), None)
        if not spec or operation not in spec['operations']:
            raise ValueError('This model/operation is not published by the selected service')
        if tools and spec.get('tools') is not True:
            raise ValueError('Tool support is unconfirmed or unavailable. Confirm it in Model Services before using this model with Agent tools.')
        if response_format and spec.get('structured_output') is not True:
            raise ValueError('Structured output support is unconfirmed for this service')
        if required_context and (spec.get('context') or 0) < required_context:
            raise ValueError('The published model context does not satisfy this request')
        if vision:
            if spec.get('vision') is not True:
                raise ValueError('Vision support is unconfirmed for this service; images were not sent elsewhere')
        params = dict(model_params or {})
        # Routing and credentials are never provider parameters. No caller can
        # turn a model invocation into arbitrary node-local HTTP access.
        if set(params) & {'model', 'messages', 'tools', 'stream', 'response_format', 'api_key', 'base_url', 'api_base', 'headers', 'extra_headers', 'extra_body', 'input'}:
            raise ValueError('Parameters cannot override model routing or credentials')
        thinking = params.pop('thinking', None)
        if thinking:
            params['reasoning_effort'] = 'medium' if thinking is True else thinking
        payload = {**params, 'model': model}
        if operation == 'embedding':
            payload['input'] = inputs
        else:
            from pantheon.utils.llm import remove_metadata
            from pantheon.utils.adapters.openai_adapter import _sanitize_tool_messages_for_chat_completions
            payload.update(messages=_sanitize_tool_messages_for_chat_completions(remove_metadata(messages)), stream=True)
            payload.setdefault('stream_options', {'include_usage': True})
            if tools:
                payload['tools'] = tools
            if response_format:
                payload['response_format'] = response_format
        async with self.connection(row, routing.get('transport_policy', 'relay_allowed'), grant) as (client, grant, chosen_transport):
            compute, billing = location(row, spec)
            route_info = {**self.source(row), 'compute_location': compute, 'billing_account': billing,
                          'compute': 'Engine on ' + (row.get('node_name') or row['node_id']) if compute == 'node' else
                              'External provider' if compute == 'provider' else 'Unconfirmed · attached engine',
                          'resolution_ms': round((time.monotonic() - route_started) * 1000), **routing, 'transport': chosen_transport}
            request_id = uuid.uuid4().hex
            route_info['request_id'] = request_id
            headers = {**({'Authorization': 'Bearer ' + grant['access_token']} if grant['access_token'] else {}),
                       'X-Model-Request': request_id, 'X-Model-Config': row['config_revision']}
            path = '/v1/embeddings' if operation == 'embedding' else '/v1/chat/completions'
            started, first = time.monotonic(), None
            result, calls, usage, finished = {'role': 'assistant', 'content': ''}, {}, {}, False
            cancel_attempted = False

            async def cancel():
                nonlocal cancel_attempted
                if cancel_attempted:
                    return
                cancel_attempted = True
                if not await self.cancel_request(row, request_id, client, grant):
                    # Disconnect remains necessary, but is not confirmation
                    # that the engine acknowledged this cancellation.
                    logger.warning('Model request {} cancellation was not acknowledged', request_id)

            events = None
            try:
                stream = client.stream('POST', grant['origin'] + path, headers=headers, json=payload)
                async with cancel_before_disconnect(stream, cancel) as response:
                    if response.status_code != 200:
                        if response.status_code in (401, 403, 409, 502, 503):
                            self.grants.clear()  # reacquire next call; never replay this request
                        raise RuntimeError(f'Model service on {row.get("node_name") or row["node_id"]} returned HTTP {response.status_code}. No fallback was sent.')
                    queue_ms = response.headers.get('X-Model-Queue-Ms', '')
                    if queue_ms.isdigit() and len(queue_ms) <= 9:
                        route_info['queue_ms'] = int(queue_ms)
                    if operation == 'embedding':
                        body = bytearray()
                        async for block in response.aiter_bytes(chunk_size=16384):
                            body.extend(block)
                            if len(body) > 16 * 1024 * 1024:
                                raise ValueError('Embedding response exceeds 16 MiB')
                        return {'data': json.loads(body), 'route': route_info}
                    # Keep the iterator alive through the cancel roundtrip.
                    # Dropping it while unwinding async-for schedules Python's
                    # async-generator finalizer, closing the real HTTP stream
                    # before /cancel has time to reach the node.
                    events = stream_events(response)
                    async for data in events:
                        if data == '[DONE]':
                            finished = True
                            break
                        chunk = json.loads(data)
                        if chunk.get('error'):
                            raise RuntimeError('The model endpoint reported a stream error')
                        if chunk.get('usage'):
                            usage = chunk['usage']
                        for choice in chunk.get('choices', []):
                            if choice.get('index', 0) != 0:
                                continue
                            delta = choice.get('delta', {})
                            if first is None and any(delta.get(k) for k in ('content', 'reasoning_content', 'reasoning', 'tool_calls')):
                                first = round((time.monotonic() - started) * 1000)
                            for key in ('content', 'reasoning_content', 'reasoning'):
                                if isinstance(delta.get(key), str):
                                    result[key] = result.get(key, '') + delta[key]
                            for call in delta.get('tool_calls') or []:
                                target = calls.setdefault(call['index'], {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                                if call.get('id'):
                                    target['id'] = call['id']
                                for key in ('name', 'arguments'):
                                    target['function'][key] += call.get('function', {}).get(key) or ''
                            if choice.get('finish_reason'):
                                result['finish_reason'] = choice['finish_reason']
                            if process_chunk and delta:
                                await process_chunk(delta)
                        result['model'] = chunk.get('model', model)
                    if not finished:
                        raise RuntimeError('Model stream ended without completion; the partial response was not retried')
            except BaseException:
                # Also cover failures while opening the stream, before its
                # response context exists. Never resubmit inference.
                await asyncio.shield(cancel())
                raise
            finally:
                if events is not None:
                    await events.aclose()
        if calls:
            result['tool_calls'] = [calls[k] for k in sorted(calls)]
        result['_metadata'] = {'model_service': {
            'deployment_id': deployment_id, **row['binding'], 'config_revision': row['config_revision'], **routing, 'transport': chosen_transport}}
        if usage:
            result['_metadata']['_debug_usage'] = usage
        result['usage'] = usage
        result['route'] = route_info
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        result['first_token_ms'] = first
        return result

    async def cancel_request(self, row, request_id, client, grant):
        """Cancel the frozen invocation, without replaying any inference data.

        A second ephemeral P2P peer can fail NAT traversal while the original
        stream is healthy. Use the existing Fleet gateway for this small control
        message; it contains only a request ID and the original config identity.
        No directory lookup, new model selection or management credential is used.
        """
        async def send(http, connection):
            headers = {'X-Model-Request': request_id, 'X-Model-Config': row['config_revision']}
            if connection['access_token']:
                headers['Authorization'] = 'Bearer ' + connection['access_token']
            response = await http.post(connection['origin'] + '/cancel', headers=headers,
                                       json={'request_id': request_id}, timeout=5)
            response.raise_for_status()
            body = response.json()
            return isinstance(body, dict) and body.get('cancelled') is True

        try:
            async with asyncio.timeout(5):
                if grant.get('_transport') == 'fleet_direct':
                    try:
                        async with asyncio.timeout(3):
                            control = await self.connect(row)
                            async with httpx.AsyncClient(transport=self.transport, timeout=3,
                                                         follow_redirects=False) as http:
                                return await send(http, control)
                    except (TimeoutError, httpx.RequestError):
                        pass
                    except (ControlError, httpx.HTTPStatusError) as error:
                        status = error.status if isinstance(error, ControlError) else error.response.status_code
                        if status not in (404, 405, 501, 502, 503, 504):
                            raise  # Stale/rejected authority never changes transport.
                    # Keep direct cancellation available when the gateway is
                    # unavailable. Only metadata is retried, under one deadline.
                return await send(client, grant)
        except Exception:
            return False


_clients = OrderedDict()


def get_client():
    hub, token = os.getenv('PANTHEON_HUB_URL', ''), os.getenv('FLEET_KEY', '')
    key = (hub, hashlib.sha256(token.encode()).hexdigest())
    if key not in _clients or _clients[key].direct_peers.retired:
        _clients[key] = ModelServices(hub, token)
        while len(_clients) > 4:
            _, previous = _clients.popitem(last=False)
            previous.direct_peers.retire()
    return _clients[key]


def model_info(ref):
    if ref.startswith('fleet-route://'):
        parse_route_ref(ref)
        key = ref
    else:
        key = model_ref(*parse_ref(ref))
    model = get_client().metadata.get(key, {})
    return {'max_input_tokens': model.get('context'), 'max_output_tokens': None,
            'supports_vision': model.get('vision'), 'supports_function_calling': model.get('tools'),
            'supports_reasoning': model.get('reasoning'), 'supports_response_schema': model.get('structured_output'),
            'input_cost_per_token': None, 'output_cost_per_token': None,
            'input_cost_per_million': None, 'output_cost_per_million': None}
