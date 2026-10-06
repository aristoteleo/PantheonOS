"""Prepared Model Services management on the ordinary App host."""
import asyncio
from collections.abc import Mapping
import ipaddress
from pathlib import Path
import ssl
from urllib.parse import urlsplit

from pantheon.apps.fleet_controller import Controller
from pantheon.apps.owned_bus import BusConfiguration, OwnedBus
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from .client import ModelServices
from .management_state import ManagementState
from .management_directory import LocalManagementDirectory
from .management_tools import ModelManagementToolSet
from .manager import ModelServiceManager


def _hub(credential, ca_pem):
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
        if ca_pem is not None and (url.scheme != 'https' or not isinstance(ca_pem, str)
                                  or not ca_pem or len(ca_pem) > 16384):
            raise ValueError
        tls = ssl.create_default_context(cadata=ca_pem)
    except (AttributeError, TypeError, ValueError, ssl.SSLError):
        raise ValueError('Model management requires explicit Hub identity and verified TLS') from None
    return ModelServices(hub=credential.endpoint, token=credential.key, tls_context=tls,
        direct_executable='', prefer_direct=False, prefetch_direct_grants=False)


class PreparedModelManagement(ModelManagementToolSet):
    """Own all management connections, never engines or paid node lifetimes."""
    def __init__(self, manager, controller):
        super().__init__(manager)
        self._controller = controller
        self._cleanup_task = None

    async def _m(self):
        self._manager.management._open()
        return await super()._m()

    async def begin_shutdown(self):
        await self._manager.management.close()

    async def _cleanup(self):
        await self.begin_shutdown()
        # register_toolset drains admitted HTTP calls before cleanup. Background
        # engine work has now joined too; only then may its connections close.
        try:
            await self._manager.client.aclose()
        finally:
            try:
                await self._manager.resolver.close()
            finally:
                await self._controller.close()

    async def cleanup(self):
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup())
        cancelled = False
        while not self._cleanup_task.done():
            try:
                await asyncio.shield(self._cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
        self._cleanup_task.result()
        if cancelled:
            raise asyncio.CancelledError


async def create_service(configuration, workspace, state):
    values = configuration.values.get('model_management')
    if (configuration.component != 'backend' or not isinstance(values, Mapping)
            or set(configuration.values) != {'model_management'}
            or not {'fleet', 'controller'} <= set(configuration.credentials) <= {'hub', 'fleet', 'controller'}
            or not {'bus'} <= set(values)
            or set(values) - {'bus', 'hub_ca_pem', 'controller_ca_pem', 'directory_root'}
            or 'hub' not in configuration.credentials and ('directory_root' not in values or 'hub_ca_pem' in values)
            or any(name in values and values[name] is None for name in ('hub_ca_pem', 'controller_ca_pem'))):
        raise ValueError('Model management requires an explicit directory, Fleet and Controller configuration')
    workspace, state = Path(workspace), Path(state)
    if not workspace.is_absolute() or not workspace.is_dir():
        raise ValueError('Model management requires its App workspace')
    bus = BusConfiguration.parse(values['bus'], configuration.credentials['fleet'])
    directory = None
    if 'directory_root' in values:
        root = values['directory_root']
        if not isinstance(root, str) or not root or not Path(root).is_absolute():
            raise ValueError('Model management requires an absolute local directory')
        directory = LocalManagementDirectory(root, owner=configuration.owner)
        await directory.deployments()  # Check existing owner-bound snapshot before opening connections.
    cloud = _hub(configuration.credentials['hub'], values.get('hub_ca_pem')) if 'hub' in configuration.credentials else None
    client = cloud
    if directory is not None:
        directory.cloud, directory.modal_available = cloud, cloud is not None
        client = directory
    controller = connection = None
    try:
        controller = Controller(configuration.credentials['controller'], values.get('controller_ca_pem'))
        owned = ManagementState(state, controller)
        connection = await OwnedBus.connect(bus, state, name='model-management-'+configuration.instance_id,
                                            inbox_prefix='_INBOX_'+configuration.owner)
        resolver = AppInstanceResolver(configuration.owner, configuration.node_id,
            configuration.owner, str(workspace), connection=connection)
        manager = ModelServiceManager(client, resolver, management=owned, group_store_root=state/'groups')
        return PreparedModelManagement(manager, controller)
    except BaseException:
        try:
            if connection is not None:
                await connection.close()
        finally:
            try:
                if controller is not None:
                    await controller.close()
            finally:
                await client.aclose()
        raise


async def register(ctx):
    state = Path(ctx.state_dir)/'model-management-private'
    if not state.parent.is_absolute() or not state.parent.is_dir() or state.is_symlink():
        raise ValueError('Model management requires private App state')
    state.mkdir(mode=0o700, exist_ok=True)
    await register_toolset(ctx, await create_service(load_runtime_configuration(required=True), ctx.workspace, state))
