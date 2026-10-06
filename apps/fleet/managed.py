"""Full Fleet tool surface with App-owned control transport and credentials."""
from collections.abc import Mapping
from pathlib import Path

from pantheon.apps.fleet_controller import Controller

from pantheon.apps.owned_bus import BusConfiguration, OwnedBus
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from .fleet import FleetToolSet
from .hpc import TOKEN_TTL_MINUTES




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
    controller = Controller(configuration.credentials.get('controller'), values.get('controller_ca_pem'),
                            join_token_ttl_minutes=TOKEN_TTL_MINUTES)
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
