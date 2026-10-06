"""Prepared App bus credentials and one explicitly owned NATS connection."""
import asyncio
import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import ssl
import tempfile
from urllib.parse import urlsplit


async def _join(task):
    """Retain ownership until cleanup finishes, even if its caller is cancelled."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


@dataclass(frozen=True)
class BusConfiguration:
    endpoint: str
    auth: str
    key: str = field(repr=False)
    tls: ssl.SSLContext | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, config, credential):
        if (not isinstance(config, Mapping) or config.keys() - {'auth', 'ca_pem'}
                or config.get('auth') not in ('creds-base64', 'token') or credential is None):
            raise ValueError('App requires a prepared bus credential and authentication type')
        try:
            url = urlsplit(credential.endpoint)
            local = url.hostname == 'localhost'
            if not local:
                try:
                    local = ipaddress.ip_address(url.hostname).is_loopback
                except ValueError:
                    pass
            valid = (url.hostname and url.scheme in ('nats', 'tls', 'ws', 'wss')
                     and (local or url.scheme in ('tls', 'wss'))
                     and url.username is None and url.password is None
                     and '?' not in credential.endpoint and '#' not in credential.endpoint
                     and (url.scheme in ('ws', 'wss') or url.path in ('', '/'))
                     and (url.port is None or 0 < url.port <= 65535)
                     and not any(c.isspace() for c in credential.endpoint))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError('App bus requires TLS except on loopback, without URL credentials')
        if not isinstance(credential.key, str) or not credential.key:
            raise ValueError('App bus credential is empty')
        if config['auth'] == 'token' and any(c.isspace() for c in credential.key):
            raise ValueError('App bus token is invalid')
        tls = None
        if 'ca_pem' in config:
            if url.scheme not in ('tls', 'wss'):
                raise ValueError('Private bus trust requires TLS')
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            tls.load_verify_locations(cadata=config['ca_pem'])
        key = credential.key
        if config['auth'] == 'creds-base64':
            try:
                key = base64.b64decode(key, validate=True).decode('ascii')
            except (ValueError, UnicodeError):
                raise ValueError('App bus JWT credential must be base64 encoded') from None
            if '-----BEGIN NATS USER JWT-----' not in key or '-----BEGIN USER NKEY SEED-----' not in key:
                raise ValueError('App bus JWT credential is incomplete')
        return cls(credential.endpoint, config['auth'], key, tls)


class OwnedBus:
    """One private connection, including any JWT credential file it needs."""
    def __init__(self):
        self._client = None
        self._credential_path = None
        self._closing = None
        self._retired = False

    @classmethod
    async def connect(cls, config, state, *, name, inbox_prefix):
        from nats.aio.client import Client
        owner = cls()
        owner._client = Client()
        try:
            auth = {'token': config.key}
            if config.auth == 'creds-base64':
                fd, path = tempfile.mkstemp(prefix='.app-bus-', suffix='.creds', dir=state)
                owner._credential_path = Path(path)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(config.key)
                auth = {'user_credentials': path}
            # No indefinite first-connect retry; preparation must report failure.
            # A restart supplies a fresh, generation-bound configuration snapshot.
            async def quiet_error(_error):
                pass  # The operation reports failure without logging credentials.
            await owner._client.connect(servers=[config.endpoint], name=name,
                inbox_prefix=inbox_prefix.encode(), tls=config.tls, error_cb=quiet_error,
                allow_reconnect=False, connect_timeout=5, drain_timeout=10, **auth)
            return owner
        except BaseException:
            await owner.close()
            raise

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def close(self):
        self._retired = True
        if self._closing is None or self._closing.done() and (self._closing.cancelled() or self._closing.exception() is not None):
            async def finish():
                if self._client is not None and not self._client.is_closed:
                    await self._client.close()
                if self._credential_path is not None:
                    self._credential_path.unlink(missing_ok=True)
                    self._credential_path = None
            self._closing = asyncio.create_task(finish())
        await _join(self._closing)
