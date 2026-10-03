"""Small consumer SDK for a Fleet-issued dependency RPC credential.

Uses no Agent imports, discovery, Fleet key, proxy environment, retries or
redirects. The owner supplies an endpoint-pinned RuntimeCredential through the
ordinary App configuration. Renewal/replacement belongs to that owner.
"""
from dataclasses import dataclass, field
import http.client
import json
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

from .runtime_config import RuntimeCredential


class DependencyCallError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, outcome_unknown: bool = False):
        super().__init__(message)
        self.status = status
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class DependencyClient:
    credential: RuntimeCredential = field(repr=False)
    tls_context: ssl.SSLContext | None = field(default=None, repr=False)

    def __post_init__(self):
        endpoint = urlsplit(self.credential.endpoint)
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password
                or endpoint.path != '/rpc' or endpoint.query or endpoint.fragment
                or not re.fullmatch(r'[a-f0-9]{64}', self.credential.key)):
            raise ValueError('Invalid dependency RPC credential')
        # Accessing port also validates malformed port syntax before any request.
        endpoint.port

    def invoke(self, method: str, args: dict[str, Any] | None = None, *, timeout_seconds: int = 60) -> Any:
        if (not isinstance(method, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', method)
                or args is not None and not isinstance(args, dict)
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600):
            raise ValueError('Invalid dependency RPC request')
        try:
            body = json.dumps({'method': method, 'args': {} if args is None else args,
                               'timeout_seconds': timeout_seconds}, allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError):
            raise ValueError('Invalid dependency RPC arguments') from None
        if len(body) > 512 * 1024:
            raise ValueError('Dependency RPC request exceeds 512 KiB')
        endpoint = urlsplit(self.credential.endpoint)
        connection = http.client.HTTPSConnection(endpoint.hostname, endpoint.port,
            context=self.tls_context, timeout=timeout_seconds + 5)
        try:
            connection.request('POST', '/rpc', body=body, headers={
                'Authorization': 'Bearer ' + self.credential.key, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                denied = response.status in (400, 401, 403, 409, 413)
                raise DependencyCallError('Dependency RPC was rejected' if denied else 'Dependency RPC failed; outcome may be unknown',
                    status=response.status, outcome_unknown=not denied)
            raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError('oversized response')
            return json.loads(raw)
        except DependencyCallError:
            raise
        except (OSError, http.client.HTTPException, ValueError, RecursionError):
            # Never expose response bodies, transport exception text, URLs or keys.
            raise DependencyCallError('Dependency RPC failed; outcome may be unknown', outcome_unknown=True) from None
        finally:
            connection.close()
