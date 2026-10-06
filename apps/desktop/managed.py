"""Prepared Desktop service: explicit files, identity and owned connections.

The deployment owner supplies both buses; tool callers cannot change them. The
event bus may be a different server/account from Fleet control. No Agent,
settings singleton, CLI login or environment-discovered bus is consulted.
"""
import asyncio
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import ssl
import time

from pantheon.apps.owned_bus import BusConfiguration, OwnedBus, _join
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from .data_server import DataServerConfig, LiveViewDataServer
from .files_binding import DesktopFilesBinding
from .fleet_binding import DesktopFleetBinding
from .session_binding import DesktopSessionBinding
from .store_binding import DesktopStoreBinding
from .toolset import DesktopToolSet


def _subject(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', value):
        raise ValueError('Desktop event namespace must be a concrete NATS subject')
    return value




class DesktopEvents:
    """Existing custom-stream wire format over a dedicated owned connection."""
    def __init__(self, bus, prefix):
        self._bus = bus
        self._prefix = _subject(prefix)
        self._closed = False

    async def publish_stream(self, stream_id, data):
        if self._closed or not self._bus.is_connected:
            raise RuntimeError('Desktop event connection is closed or disconnected')
        subject = f'{self._prefix}.pantheon.stream.{_subject(stream_id)}'
        message = {'type': 'custom', 'session_id': stream_id, 'timestamp': time.time(),
                   'data': data, 'metadata': {}}
        await self._bus.publish(subject, json.dumps(message).encode())
        # A publish queued only in a dying client is not useful to viewports.
        await self._bus.flush(timeout=5)
        return True

    async def close(self):
        self._closed = True
        await self._bus.close()


def _prepare(configuration, workspace, state, *, data_port=0):
    """Validate all deployment policy before using credentials or opening buses."""
    config = configuration.values.get('desktop')
    required = {'user_seed', 'fleet', 'events', 'event_prefix', 'catalog', 'data_roots', 'store', 'data'}
    if not isinstance(config, Mapping) or set(config) != required:
        raise ValueError('Desktop needs an explicit complete deployment configuration')
    if not configuration.owner or not configuration.node_id or not isinstance(config['user_seed'], str) or not config['user_seed']:
        raise ValueError('Desktop requires explicit Fleet, node and user identities')
    _subject(configuration.owner)
    _subject(config['event_prefix'])
    fleet = BusConfiguration.parse(config['fleet'], configuration.credentials.get('fleet'))
    events = BusConfiguration.parse(config['events'], configuration.credentials.get('events'))
    def absolute(value):
        path = Path(value)
        if not path.is_absolute():
            raise ValueError('Desktop deployment paths must be absolute')
        return path.resolve()
    workspace, state = absolute(workspace), absolute(state)
    if not workspace.is_dir() or not state.is_dir():
        raise ValueError('Desktop workspace and private state must exist')
    if not isinstance(config['catalog'], (list, tuple)) or not isinstance(config['data_roots'], (list, tuple)):
        raise ValueError('Desktop catalogs and data roots must be lists')
    roots = []
    for item in config['catalog']:
        if not isinstance(item, Mapping) or set(item) != {'path', 'scope'}:
            raise ValueError('Desktop catalog must specify path and scope')
        roots.append((absolute(item['path']), item['scope']))
    if (any(scope not in ('user', 'workspace', 'builtin') for _, scope in roots)
            or sum(scope == 'user' for _, scope in roots) != 1):
        raise ValueError('Desktop requires exactly one user catalog')
    # Include ordinary versioned Store locations, never its credentials/state.
    served = [workspace, *(root for root, _ in roots), *(absolute(root) for root in config['data_roots'])]
    for root, scope in roots:
        if scope == 'user':
            served.extend((root.parent / 'app-store' / name).resolve() for name in ('snapshots', 'forks', 'repositories'))
    if any(state.is_relative_to(root) or root.is_relative_to(state) for root in served):
        raise ValueError('Desktop private state must be separate from every served directory')
    store = config['store']
    if not isinstance(store, Mapping) or store.keys() - {'origin', 'credential', 'ca_pem'} or 'origin' not in store:
        raise ValueError('Desktop requires an explicit Store origin')
    token, trust = '', None
    if 'credential' in store:
        credential = configuration.credentials.get('store')
        if store['credential'] != 'store' or credential is None or credential.endpoint.rstrip('/') != store['origin'].rstrip('/'):
            raise ValueError('Desktop Store credential belongs to another origin')
        token = credential.key
    if 'ca_pem' in store:
        trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trust.load_verify_locations(cadata=store['ca_pem'])
    store = DesktopStoreBinding(store['origin'], token, trust)
    data = config['data']
    if not isinstance(data, Mapping) or data.get('mode') not in ('loopback', 'tunnel'):
        raise ValueError('Desktop needs an explicit data delivery mode')
    if data['mode'] == 'loopback':
        if set(data) != {'mode'} or data_port:
            raise ValueError('Loopback Desktop data cannot use a public assigned port')
        token, base = None, None
    else:
        credential = configuration.credentials.get('data')
        if set(data) != {'mode', 'credential'} or data['credential'] != 'data' or credential is None or not data_port:
            raise ValueError('Tunneled Desktop data needs an assigned port and endpoint credential')
        # Reuse the origin policy: TLS in deployment, HTTP only on loopback.
        base = DesktopStoreBinding(credential.endpoint).origin
        token = credential.key
    server_config = DataServerConfig(token=token, port=data_port)
    return config, workspace, state, roots, served, fleet, events, store, server_config, base


async def create_service(configuration, workspace, state, *, data_port=0):
    config, workspace, state, roots, served, fleet, events, store, server_config, base = _prepare(
        configuration, workspace, state, data_port=data_port)
    buses = []
    service = None
    try:
        for name, spec in (('fleet', fleet), ('events', events)):
            buses.append(await OwnedBus.connect(spec, state, name=f'desktop-{name}-{configuration.instance_id}',
                inbox_prefix='_INBOX_' + configuration.owner))
        publisher = DesktopEvents(buses[1], config['event_prefix'])
        server = LiveViewDataServer(config=server_config)
        if base is not None:
            server.set_tunnel_base(base)
        service = DesktopToolSet(
            session_binding=DesktopSessionBinding(state, publisher),
            files_binding=DesktopFilesBinding(workspace=workspace, app_roots=roots,
                data_roots=served, server=server, node_id=configuration.node_id),
            fleet_binding=DesktopFleetBinding(fleet_id=configuration.owner, node_id=configuration.node_id,
                user_seed=config['user_seed'], workspace=workspace, connection=buses[0]),
            store_binding=store)
        return service
    except BaseException:
        async def dispose():
            errors = []
            if service is not None:
                try:
                    await service.cleanup()
                except BaseException as exc:
                    errors.append(exc)
            for bus in reversed(buses):
                try:
                    await bus.close()
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                raise BaseExceptionGroup('Desktop startup cleanup failed', errors)
        await _join(asyncio.create_task(dispose()))
        raise


async def register(ctx):
    configuration = load_runtime_configuration(required=True)
    value = os.environ.get('PANTHEON_PORT_DATA', '0')
    if not value.isdecimal():
        raise ValueError('Desktop data port must be assigned by Fleet')
    # The portable host's default workspace is DATA/workspace. Keep documents
    # and bus credentials in a sibling private directory, never under it.
    state = Path(ctx.state_dir) / 'desktop-private'
    if not state.parent.is_absolute() or not state.parent.is_dir() or state.is_symlink():
        raise ValueError('Desktop requires an existing owned App data directory')
    state.mkdir(mode=0o700, exist_ok=True)
    service = await create_service(configuration, ctx.workspace, state, data_port=int(value))
    await register_toolset(ctx, service)
