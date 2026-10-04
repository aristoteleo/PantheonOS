"""Owner-only control client for consumer-scoped Model Service dependencies.

No engine, prompt transport, global settings, discovery or ambient credentials.
The existing Hub directory and generic App HTTP grant API remain authoritative.
"""
import asyncio
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

import httpx

from pantheon.apps.dependency_assembly import AssemblyError, _identity
from pantheon.models.errors import ControlError


def inference_rules():
    # Full published connector authority: text, embeddings, typed multimodal
    # jobs and media. Model/engine management and /rpc are deliberately absent.
    return [
        {'method': method, 'path': path, 'prefix': prefix}
        for path, methods, prefix in (
            ('/v1/chat/completions', ('POST',), False),
            ('/v1/embeddings', ('POST',), False),
            ('/cancel', ('POST',), False),
            ('/route-state', ('GET',), False),
            ('/inference/jobs', ('GET', 'POST', 'DELETE'), True),
            ('/media/artifacts', ('GET', 'POST', 'PUT', 'DELETE'), True),
        ) for method in methods
    ]


class ModelDependencyControl:
    def __init__(self, *, owner, credential, tls_context=None, transport=None):
        try:
            parts = urlsplit(credential.endpoint)
            if (not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', owner)
                    or parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment or not credential.key):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise AssemblyError('Supply an explicit HTTPS model owner credential') from None
        self.owner, self.credential = owner, credential
        self.endpoint = credential.endpoint.rstrip('/')
        self.http = httpx.AsyncClient(timeout=25, trust_env=False, follow_redirects=False,
            verify=tls_context or True, transport=transport,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=4))
        self._slots = asyncio.Semaphore(8)
        self._closed = False

    async def hub_request(self, method, path, data=None):
        allowed = (method == 'GET' and path in {'/api/model-services', '/api/model-services/routes'}
                   or method == 'POST' and (path == '/api/fleet/apps/dependency-http-grants'
                       or re.fullmatch(r'/api/model-services/routes/[a-z0-9][a-z0-9_-]{0,63}/resolve', path)
                       or re.fullmatch(r'/api/model-services/[a-z0-9][a-z0-9_-]{0,63}/engine-idle', path)))
        if not allowed:
            raise ControlError(403)
        async with self._slots:
            if self._closed:
                raise ControlError(503)
            try:
                async with self.http.stream(method, self.endpoint + path, json=data,
                        headers={'Authorization': 'Bearer ' + self.credential.key}) as response:
                    if response.status_code != 200:
                        raise ControlError(response.status_code if 400 <= response.status_code <= 599 else 502)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 4 * 1024 * 1024:
                            raise ControlError(502)
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ControlError(502)
                    return result
            except (httpx.HTTPError, ValueError, TypeError):
                # The caller never receives owner URLs, headers or response bodies.
                raise ControlError(503) from None

    async def deployments(self):
        return (await self.hub_request('GET', '/api/model-services'))['deployments']

    async def routes(self):
        return (await self.hub_request('GET', '/api/model-services/routes'))['routes']

    async def issue_connection(self, *, consumer, deployment, peer_id=None):
        # Do not exchange an owner workload-direct token: its lifetime isn't
        # consumer-bound. Direct-only callers must fail until that path is ready.
        if peer_id is not None:
            raise ControlError(503)
        provider = deployment['binding']
        _identity(consumer)
        _identity(provider, provider=True)
        request = {'consumer': consumer, 'provider': provider, 'app_id': 'model-service',
                   'rules': inference_rules(), 'ttl_seconds': 300}
        # Retries within a short window reuse the receipt, including after host
        # restart. The window is much shorter than TTL and the client's 30s
        # refresh margin, so near-expiry refresh obtains a new authority.
        identity = json.dumps({'owner': self.owner, **request, 'window': int(time.time()) // 30},
                              sort_keys=True, separators=(',', ':')).encode()
        request['operation_id'] = 'model-' + hashlib.sha256(identity).hexdigest()
        grant = await self.hub_request('POST', '/api/fleet/apps/dependency-http-grants', request)
        try:
            origin = urlsplit(grant['origin'])
            expected = hashlib.sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
            if (grant['consumer'] != {'fleet_id': self.owner, **consumer}
                    or grant['provider'] != {'fleet_id': self.owner, **provider}
                    or origin.scheme != 'https' or not origin.hostname or not origin.hostname.startswith(expected + '.')
                    or origin.username or origin.password or origin.port is not None
                    or origin.path or origin.query or origin.fragment
                    or type(grant['expires']) is not int or not time.time() + 30 < grant['expires'] <= int(time.time()) + 300
                    or not all(isinstance(grant[k], str) and re.fullmatch(r'[a-f0-9]{64}', grant[k])
                               for k in ('access_token', 'grant_id'))
                    or grant['access_token'] == grant['grant_id']):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ControlError(502) from None
        return {k: grant[k] for k in ('origin', 'access_token', 'expires')}

    async def aclose(self):
        self._closed = True
        await self.http.aclose()
