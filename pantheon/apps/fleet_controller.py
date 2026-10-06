"""Explicit Controller transport shared by ordinary management Apps."""
import ipaddress
import ssl
from urllib.parse import urlsplit

import httpx


class Controller:
    """Prepared Controller authority; never discovers CLI login or environment."""
    def __init__(self, credential, ca_pem=None, *, join_token_ttl_minutes=None):
        try:
            url = urlsplit(credential.endpoint)
            local = url.hostname == 'localhost'
            if not local:
                try:
                    local = ipaddress.ip_address(url.hostname).is_loopback
                except ValueError:
                    pass
            if (not url.hostname or url.scheme not in ('http', 'https')
                    or url.scheme == 'http' and not local or url.username or url.password
                    or url.path not in ('', '/') or url.query or url.fragment
                    or any(c.isspace() for c in credential.endpoint)
                    or url.port is not None and not 0 < url.port <= 65535
                    or not isinstance(credential.key, str) or not credential.key):
                raise ValueError
            tls = True
            if ca_pem is not None:
                if url.scheme != 'https' or not isinstance(ca_pem, str) or not ca_pem or len(ca_pem) > 16384:
                    raise ValueError
                tls = ssl.create_default_context(cadata=ca_pem)
        except (AttributeError, TypeError, ValueError, ssl.SSLError):
            raise ValueError('Fleet requires an explicit Controller origin, credential and valid TLS trust') from None
        if join_token_ttl_minutes is not None and (type(join_token_ttl_minutes) is not int
                or not 1 <= join_token_ttl_minutes <= 10080):
            raise ValueError('Invalid Controller join-token lifetime')
        self._join_token_ttl_minutes = join_token_ttl_minutes
        self._key = credential.key
        self._http = httpx.AsyncClient(base_url=credential.endpoint.rstrip('/'), verify=tls,
            trust_env=False, follow_redirects=False, timeout=10,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=4))

    async def request(self, path, body):
        # Only the explicit control operations shared by prepared management Apps.
        allowed = {'/join-tokens': {'ttl_minutes'}, '/revoke': {'node_id'}}
        if path not in allowed or not isinstance(body, dict) or body.keys() - allowed[path]:
            raise ValueError('Unsupported prepared Controller operation')
        response = await self._http.post(path, json={**body, 'key': self._key})
        response.raise_for_status()
        return response.json()

    async def mint_join_token(self):
        body = {} if self._join_token_ttl_minutes is None else {'ttl_minutes': self._join_token_ttl_minutes}
        return (await self.request('/join-tokens', body))['join_token']

    async def latest_release(self):
        response = await self._http.get('/fleet/latest')
        if response.status_code != 200:
            return ''
        return str(response.json().get('tag') or '')

    async def close(self):
        await self._http.aclose()

