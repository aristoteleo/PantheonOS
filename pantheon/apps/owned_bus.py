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


def _bus_url(value):
    """TLS (tls/wss) everywhere except loopback; never credentials in the URL."""
    try:
        url = urlsplit(value)
        local = url.hostname == 'localhost'
        if not local:
            try:
                local = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                pass
        return bool(url.hostname and url.scheme in ('nats', 'tls', 'ws', 'wss')
                    and (local or url.scheme in ('tls', 'wss'))
                    and url.username is None and url.password is None
                    and '?' not in value and '#' not in value
                    and (url.scheme in ('ws', 'wss') or url.path in ('', '/'))
                    and (url.port is None or 0 < url.port <= 65535)
                    and not any(c.isspace() for c in value))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class BusConfiguration:
    endpoint: str
    auth: str
    key: str = field(repr=False)
    tls: ssl.SSLContext | None = field(default=None, repr=False)
    # fleet-key: the credential is an owner platform key at the controller;
    # bus credentials are minted from it and renewed before they expire.
    controller: str | None = None

    @classmethod
    def parse(cls, config, credential):
        if isinstance(config, Mapping) and config.get('auth') == 'fleet-key':
            return cls._fleet_key(config, credential)
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

    @classmethod
    def _fleet_key(cls, config, credential):
        if config.keys() - {'auth', 'url'} or credential is None or not _bus_url(config.get('url') or ''):
            raise ValueError('Fleet key bus needs a TLS bus URL and an owner platform key credential')
        controller = urlsplit(credential.endpoint or '')
        if (controller.scheme != 'https' or not controller.hostname or controller.username
                or controller.password or controller.query or controller.fragment):
            raise ValueError('Fleet key bus credential must be bound to the HTTPS controller')
        if not isinstance(credential.key, str) or not credential.key.startswith('pbk_') or any(
                c.isspace() for c in credential.key):
            raise ValueError('Fleet key bus credential must be an owner platform key')
        return cls(config['url'], 'fleet-key', credential.key, None, credential.endpoint.rstrip('/'))


def _creds_parts(text):
    def section(begin):
        start = text.index(begin) + len(begin)
        return text[start:text.index('------END', start)].strip()
    try:
        return section('-----BEGIN NATS USER JWT-----'), section('-----BEGIN USER NKEY SEED-----')
    except ValueError:
        raise ValueError('Controller returned an incomplete bus credential') from None


def _expires(jwt):
    import json
    try:
        payload = jwt.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        return float(claims['exp'])
    except (IndexError, KeyError, ValueError, TypeError):
        return None


def _patch_websocket_close():
    """Two nats-py WebSocket transport defects.

    It hands a CLOSE/ERROR frame's payload (an int) to its parser, which kills
    the read loop instead of reconnecting. The server sends exactly such a frame
    when an owner credential's JWT expires. Treat these frames as end-of-stream,
    as the library already does for CLOSED. And it cannot close a transport
    whose WebSocket never opened (below)."""
    try:
        import aiohttp
        from nats.aio.transport import WebSocketTransport
    except ImportError:
        return
    if getattr(WebSocketTransport.readline, '_pantheon_close_safe', False):
        return
    original = WebSocketTransport.readline
    ends = {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR}

    async def readline(self):
        data = await self._ws.receive()
        if data.type == aiohttp.WSMsgType.CLOSED or data.type in ends:
            return b''
        return data.data
    readline._pantheon_close_safe = True
    readline.__wrapped__ = original
    WebSocketTransport.readline = readline

    # A WebSocket that never opened leaves its close Future unresolved, so
    # closing the client after a failed connect would wait forever.
    original_wait_closed = WebSocketTransport.wait_closed

    async def wait_closed(self):
        if self._ws is None and not self._close_task.done():
            if self._client:
                await self._client.close()
            self._client = None
            return
        await original_wait_closed(self)
    wait_closed.__wrapped__ = original_wait_closed
    WebSocketTransport.wait_closed = wait_closed


class FleetKeyCredential:
    """Mints and renews this App's bus credential from its owner platform key."""
    RENEW_FRACTION = 0.6  # renew well before expiry; a reconnect picks up the new JWT

    def __init__(self, controller, key, *, transport=None):
        self.controller, self._key, self._transport = controller, key, transport
        self.fleet_id = self._jwt = self._seed = None
        self._expires = None

    async def join(self):
        import httpx
        async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False,
                                     transport=self._transport) as client:
            response = await client.post(self.controller + '/join', json={'key': self._key})
        if response.status_code != 200:
            raise ValueError(f'Controller refused the bus credential ({response.status_code})')
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('creds'), str) or not data.get('fleet_id'):
            raise ValueError('Controller returned no bus credential')
        if self.fleet_id not in (None, data['fleet_id']):
            raise ValueError('Controller changed the Fleet owner of this bus credential')
        self.fleet_id = data['fleet_id']
        self._jwt, self._seed = _creds_parts(data['creds'])
        self._expires = _expires(self._jwt)

    def renew_after(self, now):
        if self._expires is None:
            return 300.0
        return max(5.0, (self._expires - now) * self.RENEW_FRACTION)

    def user_jwt(self):
        return bytearray(self._jwt.encode())

    def sign(self, nonce):
        import nkeys
        key = nkeys.from_seed(bytearray(self._seed.encode()))
        try:
            return base64.b64encode(key.sign(nonce.encode()))
        finally:
            key.wipe()


class OwnedBus:
    """One private connection, including any JWT credential file it needs."""
    FIRST_CONNECT_ATTEMPTS = 3

    def __init__(self):
        self._client = None
        self._credential_path = None
        self._closing = None
        self._retired = False
        self._renewal = None

    @classmethod
    async def connect(cls, config, state, *, name, inbox_prefix):
        from nats.aio.client import Client
        owner = cls()
        owner._client = Client()
        try:
            if config.auth == 'fleet-key':
                await owner._connect_fleet_key(config, name=name, inbox_prefix=inbox_prefix)
                return owner
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

    async def _connect_fleet_key(self, config, *, name, inbox_prefix, transport=None):
        """Renewing owner credential: the bus survives its hourly JWT expiry.

        The first connect gives up after a few attempts with the last error.
        Afterwards the client reconnects indefinitely (the server closes an
        expired JWT) using the credential renewed below.
        """
        import time
        from pantheon.utils.log import logger
        _patch_websocket_close()
        credential = FleetKeyCredential(config.controller, config.key, transport=transport)
        await credential.join()
        prefix = inbox_prefix if inbox_prefix else '_INBOX_' + credential.fleet_id
        errors = []

        async def record_error(error):
            errors.append(error)
            logger.warning(f'[owned-bus] {name}: {type(error).__name__}: {error}')
        # nats-py retries a failed first connect forever when reconnects are
        # unlimited, silently; bound it so a startup failure is reported.
        try:
            await self._client.connect(servers=[config.endpoint], name=name, inbox_prefix=prefix.encode(),
                error_cb=record_error, user_jwt_cb=credential.user_jwt, signature_cb=credential.sign,
                allow_reconnect=True, max_reconnect_attempts=self.FIRST_CONNECT_ATTEMPTS, reconnect_time_wait=2,
                connect_timeout=10, drain_timeout=10)
        except Exception as exc:
            last = errors[-1] if errors else exc
            raise ConnectionError(f'Cannot reach the App bus at {config.endpoint}: {last}') from exc
        self._client.options['max_reconnect_attempts'] = -1

        async def renew():
            while not self._retired:
                await asyncio.sleep(credential.renew_after(time.time()))
                try:
                    await credential.join()
                except Exception:
                    await asyncio.sleep(30)  # keep the current JWT; retry until it expires
                    continue
                await self._reauthenticate()
        self._renewal = asyncio.create_task(renew())

    async def _reauthenticate(self):
        """Present the renewed JWT before the old one expires.

        nats-py permanently closes a connection when the server reports an
        expired credential, so waiting for expiry is not an option. A
        client-side reconnect re-runs the JWT/nonce handshake through the
        callbacks and restores subscriptions.
        """
        from nats import errors
        client = self._client
        if client is None or client.is_closed or not client.is_connected:
            return
        await client._process_op_err(errors.UnexpectedEOF())

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def close(self):
        self._retired = True
        if self._closing is None or self._closing.done() and (self._closing.cancelled() or self._closing.exception() is not None):
            async def finish():
                if self._renewal is not None:
                    self._renewal.cancel()
                if self._client is not None and not self._client.is_closed:
                    await self._client.close()
                if self._credential_path is not None:
                    self._credential_path.unlink(missing_ok=True)
                    self._credential_path = None
            self._closing = asyncio.create_task(finish())
        await _join(self._closing)
