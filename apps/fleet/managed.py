"""Full Fleet tool surface with App-owned control transport and credentials."""
from collections.abc import Mapping
import ipaddress
from pathlib import Path
import ssl
from urllib.parse import urlsplit

import httpx

from pantheon.apps.owned_bus import BusConfiguration, OwnedBus
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from .fleet import FleetToolSet
from .hpc import TOKEN_TTL_MINUTES


class Controller:
    """Prepared Controller authority; never discovers CLI login or environment."""
    def __init__(self, credential, ca_pem=None):
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
        self._key = credential.key
        self._http = httpx.AsyncClient(base_url=credential.endpoint.rstrip('/'), verify=tls,
            trust_env=False, follow_redirects=False, timeout=10,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=4))

    async def mint_join_token(self):
        response = await self._http.post('/join-tokens', json={'key': self._key, 'ttl_minutes': TOKEN_TTL_MINUTES})
        response.raise_for_status()
        return response.json()['join_token']

    async def latest_release(self):
        response = await self._http.get('/fleet/latest')
        if response.status_code != 200:
            return ''
        return str(response.json().get('tag') or '')

    async def close(self):
        await self._http.aclose()


async def create_service(configuration, workspace, state):
    values = configuration.values.get('fleet')
    if (not isinstance(values, Mapping) or set(values) - {'bus', 'controller_ca_pem'}
            or 'bus' not in values or 'controller_ca_pem' in values and values['controller_ca_pem'] is None):
        raise ValueError('Fleet requires explicit prepared bus and Controller settings')
    workspace, state = Path(workspace), Path(state)
    if (not workspace.is_absolute() or not workspace.is_dir()
            or not state.is_absolute() or not state.is_dir()):
        raise ValueError('Fleet workspace and private state must be existing absolute directories')
    bus = BusConfiguration.parse(values['bus'], configuration.credentials.get('fleet'))
    controller = Controller(configuration.credentials.get('controller'), values.get('controller_ca_pem'))
    connection = None
    try:
        connection = await OwnedBus.connect(bus, state, name='fleet-app-'+configuration.instance_id,
                                            inbox_prefix='_INBOX_'+configuration.owner)
        resolver = AppInstanceResolver(configuration.owner, configuration.node_id,
            configuration.owner, str(workspace), connection=connection)
        return FleetToolSet(resolver=resolver, controller=controller)
    except BaseException:
        try:
            if connection is not None:
                await connection.close()
        finally:
            await controller.close()
        raise


async def register(ctx):
    config = load_runtime_configuration(required=True)
    state = Path(ctx.state_dir) / 'fleet-private'
    if not state.parent.is_absolute() or not state.parent.is_dir() or state.is_symlink():
        raise ValueError('Fleet requires its private App state directory')
    state.mkdir(mode=0o700, exist_ok=True)
    await register_toolset(ctx, await create_service(config, ctx.workspace, state))
