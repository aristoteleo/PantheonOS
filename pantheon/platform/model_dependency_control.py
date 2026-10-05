"""Owner-only control client for consumer-scoped Model Service dependencies.

No engine, prompt transport, global settings, discovery or ambient credentials.
Directory reads use Hub or an explicitly supplied local owner journal. The
existing generic App HTTP grant API remains the inference transport authority.
"""
import asyncio
import hashlib
import json
import re
import ssl
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
    def __init__(self, *, owner, credential, tls_context=None, transport=None, http_origin=None, directory=None):
        try:
            parts = urlsplit(credential.endpoint)
            if (not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', owner)
                    or parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment or not credential.key):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise AssemblyError('Supply an explicit HTTPS model owner credential') from None
        if http_origin is not None:
            match = re.fullmatch(r'https://127\.0\.0\.1:([1-9][0-9]{0,4})', http_origin) if isinstance(http_origin, str) else None
            if (not match or int(match[1]) > 65535 or credential.endpoint != http_origin
                    or not isinstance(tls_context, ssl.SSLContext) or not tls_context.check_hostname
                    or tls_context.verify_mode != ssl.CERT_REQUIRED):
                raise AssemblyError('Local model HTTP requires an explicit loopback issuer and private TLS trust')
        if directory is not None:
            from pantheon.models.local_directory import LocalModelDirectory
            if (http_origin is None or not isinstance(directory, LocalModelDirectory)
                    or directory.owner != owner or not directory.read_only):
                raise AssemblyError('A local model directory requires the same local owner and read-only access')
        self.directory = directory
        self.http_origin = http_origin
        self.owner, self.credential = owner, credential
        self.endpoint = credential.endpoint.rstrip('/')
        self.http = httpx.AsyncClient(timeout=25, trust_env=False, follow_redirects=False,
            verify=tls_context or True, transport=transport,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=4))
        self._slots = asyncio.Semaphore(8)
        self._closed = False

    async def hub_request(self, method, path, data=None):
        allowed = (method == 'GET' and path in {'/api/model-services', '/api/model-services/routes'}
                   or method == 'POST' and (path in {'/api/fleet/apps/dependency-http-grants', '/api/fleet/apps/dependency-direct-grants'}
                       or re.fullmatch(r'/api/model-services/routes/[a-z0-9][a-z0-9_-]{0,63}/resolve', path)
                       or re.fullmatch(r'/api/model-services/[a-z0-9][a-z0-9_-]{0,63}/engine-idle', path)))
        if not allowed:
            raise ControlError(403)
        async with self._slots:
            if self._closed:
                raise ControlError(503)
            if self.directory is not None and path.startswith('/api/model-services'):
                # Directory reads and alias planning stay on this local profile.
                # Inference grants still go to the authenticated Controller.
                # Bound file reads too; never fall back to an ambient Hub.
                return await self.directory.hub_request(method, path, data)
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
        if self.http_origin is not None and peer_id is not None:
            raise ControlError(403)
        if peer_id is not None and (not isinstance(peer_id, str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,128}', peer_id)):
            raise ControlError(400)
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
        path = '/api/fleet/apps/dependency-http-grants'
        if peer_id is not None:
            request['peer_id'] = peer_id
            path = '/api/fleet/apps/dependency-direct-grants'
        grant = await self.hub_request('POST', path, request)
        try:
            if (grant['consumer'] != {'fleet_id': self.owner, **consumer}
                    or grant['binding' if peer_id is not None else 'provider'] != {'fleet_id': self.owner, **provider}
                    or type(grant['expires']) is not int or not time.time() + 30 < grant['expires'] <= int(time.time()) + 300
                    or not all(isinstance(grant[k], str) and re.fullmatch(r'[a-f0-9]{64}', grant[k])
                               for k in ('access_token', 'grant_id'))
                    or grant['access_token'] == grant['grant_id']):
                raise ValueError
            if peer_id is not None:
                node_peer, addresses = grant['peer_id'], grant['addresses']
                if (grant['transport'] != 'fleet_direct' or not isinstance(node_peer, str)
                        or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,128}', node_peer)
                        or not isinstance(addresses, list) or not 1 <= len(addresses) <= 32
                        or any(not isinstance(a, str) or len(a) > 1024 or not a.startswith('/')
                               or '/p2p-circuit' in a or not a.endswith('/p2p/' + node_peer) for a in addresses)):
                    raise ValueError
            else:
                origin = urlsplit(grant['origin'])
                expected = hashlib.sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
                bound = (origin.hostname and origin.hostname.startswith(expected + '.') and origin.port is None)
                if self.http_origin is not None:
                    bound = grant['origin'] == self.http_origin
                if (origin.scheme != 'https' or not bound or origin.username or origin.password
                        or origin.path or origin.query or origin.fragment
                        or '?' in grant['origin'] or '#' in grant['origin']):
                    raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ControlError(502) from None
        if peer_id is not None:
            return {k: grant[k] for k in ('binding', 'peer_id', 'addresses', 'access_token', 'expires', 'transport')}
        return {k: grant[k] for k in ('origin', 'access_token', 'expires')}

    async def aclose(self):
        self._closed = True
        await self.http.aclose()
