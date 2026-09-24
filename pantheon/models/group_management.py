"""Owner-facing creation and continuation of immutable distributed models."""
import asyncio
from copy import deepcopy
import hashlib
import re

from .group_creation import CreationJournal, CreationCoordinator
from .group_hub import HubGroupJournal
from .group_coordinator import GroupCoordinator
from .group_package import GroupPackageStore
from .group_network import addresses
from pantheon.apps.lifecycle import FleetLifecycle


def package_store(manager):
    from pantheon.settings import get_settings
    root = manager.group_store_root
    if root is None:
        root = get_settings().pantheon_dir / 'model-groups' / manager.resolver._fleet
    return GroupPackageStore(root)


PLATFORM_CAPABILITY = 'model-group-platform-network'


async def create(manager, journal, group_id, config):
    # Opt-in provider network (e.g. Modal i6pn): process ranks, no Fleet overlay.
    platform = isinstance(config, dict) and config.get('network_mode') == 'platform-private'
    keys = {'model_sha256', 'context_length', 'parallel', 'members'} | ({'network_mode'} if platform else set())
    if (not isinstance(group_id, str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', group_id)
            or not isinstance(config, dict) or set(config) != keys
            or not isinstance(config['members'], list) or not 2 <= len(config['members']) <= 8
            or not isinstance(config['model_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', config['model_sha256'])
            or type(config['context_length']) is not int or not 512 <= config['context_length'] <= 1048576
            or type(config['parallel']) is not int or not 1 <= config['parallel'] <= 16):
        raise ValueError('Choose an original group name, model snapshot, context, parallelism and 2–8 nodes')
    # Existing intent is never overwritten, even if its response was lost.
    try:
        await journal.load(group_id)
    except KeyError:
        pass
    else:
        raise ValueError('This group already has a saved deployment; refresh and continue it')
    rows = {r['deployment_id']: r for r in await manager.client.deployments()}
    selected, nodes, underlay = [], set(), []
    for member in config['members']:
        if (not isinstance(member, dict)
                or set(member) != ({'deployment_id', 'resources'} if platform else {'deployment_id', 'underlay', 'resources'})
                or not isinstance(member['deployment_id'], str)):
            raise ValueError('Select a snapshot service, private UDP endpoint and resource budgets for every node')
        row = rows.get(member['deployment_id'])
        if (not row or row.get('mode') != 'managed' or row.get('engine') != 'sglang'
                or row.get('state') not in {'draft', 'ready'} or not row.get('binding')
                or row['node_id'] in nodes):
            raise ValueError('Choose distinct nodes with configured owned SGLang snapshot connectors')
        nodes.add(row['node_id'])
        if not platform:
            underlay.append(member['underlay'])
        selected.append((row, member))
    if not platform:
        addresses(underlay, len(selected))
    scopes = set()
    lifecycle = FleetLifecycle(manager.resolver)
    members, records, device_ids = [], [], set()
    for rank, (row, member) in enumerate(selected):
        node = await manager.node(row['node_id'], managed=True)
        caps = node.get('capability') or {}
        runtimes = caps.get('runtimes') or {}
        if platform:
            if (caps.get('os') != 'linux' or caps.get('arch') != 'amd64'
                    or PLATFORM_CAPABILITY not in (caps.get('caps') or [])
                    or runtimes.get('group-platform-network') != 'modal-i6pn'):
                raise ValueError(f"{row['node_id']}: start Fleet with --group-platform-network=modal-i6pn on a Modal GPU node")
            scopes.add(runtimes.get('group-platform-scope'))
            if len(scopes) != 1:
                raise ValueError('Every rank must run in the same Modal app and region')
        elif (caps.get('os') != 'linux' or caps.get('arch') != 'amd64'
                or 'model-group-private-network' not in (caps.get('caps') or [])):
            raise ValueError(f"{row['node_id']}: update Fleet for private model groups on Linux NVIDIA nodes")
        state = await lifecycle.status(row['node_id'])
        if (state.get('owner') != journal.owner or state.get('node_id') != row['node_id']
                or any(i.get('scope') == 'model-group-' + group_id for i in (state.get('instances') or {}).values())
                or any(o.get('request', {}).get('scope') == 'model-group-' + group_id for o in (state.get('operations') or {}).values())):
            raise ValueError('This group scope already has node state; inspect the original deployment')
        status = await lifecycle.resource_status(row['node_id'])
        accelerators = {d['id']: d for d in (status.get('inventory') or {}).get('accelerators', [])}
        resource = member['resources']
        if (not isinstance(resource, dict) or set(resource) != {'memory_bytes', 'devices'}
                or not isinstance(resource['devices'], list) or not 1 <= len(resource['devices']) <= 4
                or type(resource['memory_bytes']) is not int or not 256 << 20 <= resource['memory_bytes'] <= 1 << 50):
            raise ValueError('Declare system memory and exclusive NVIDIA GPU budgets')
        physical = []
        for device in resource['devices']:
            if (not isinstance(device, dict) or set(device) != {'id', 'backend', 'memory_bytes', 'exclusive'}
                    or not isinstance(device['id'], str) or not re.fullmatch(r'GPU-[A-Za-z0-9-]{1,124}', device['id'])
                    or device['id'] in device_ids
                    or device['backend'] != 'cuda' or device['exclusive'] is not True
                    or type(device['memory_bytes']) is not int or not 256 << 20 <= device['memory_bytes'] <= 1 << 50):
                raise ValueError('Declare exclusive NVIDIA GPU budgets')
            device_ids.add(device['id'])
            measured = accelerators.get(device['id'], {})
            total = (measured.get('memory') or {}).get('total_bytes')
            if measured.get('backend') != 'cuda' or type(total) is not int or total < device['memory_bytes']:
                raise ValueError('The selected GPU capacity is unknown or below its requested budget')
            physical.append(total)
        # Return only a bounded, non-executable public receipt; no weights/paths/secrets.
        record = await manager.rpc(row['binding'], 'group_snapshot', {'sha256': config['model_sha256']})
        from .managed import module
        record = module('group_model').descriptor(record)
        if record['sha256'] != config['model_sha256'] or records and record != records[0]:
            raise ValueError('Every selected node must have the exact same prepared model snapshot')
        records.append(record)
        members.append(dict(rank=rank, node_id=row['node_id'], generation=2,
            address=runtimes.get('group-platform-address') if platform else f'10.231.0.{rank+1}',
            control_port=18407, interface=runtimes.get('group-platform-interface') if platform else 'wg0',
            resources=deepcopy(resource), physical_gpu_bytes=physical))
    tp = sum(len(m['resources']['devices']) for m in members)
    if tp not in {2, 4, 8} or any(len(m['resources']['devices']) != tp // len(members) for m in members):
        raise ValueError('Use 2, 4 or 8 GPUs, distributed equally across the selected nodes')
    plan = dict(protocol=1, owner=journal.owner, group_id=group_id,
        recipe_id='sglang-0.5.20-linux-amd64-process' if platform else 'sglang-0.5.20-linux-amd64',
        model_sha256=config['model_sha256'],
        context_length=config['context_length'], parallel=config['parallel'],
        tensor_parallel_size=tp, rendezvous_port=18408, inference_protocol=1,
        members=members, **({'network_mode': 'platform-private'} if platform else {'underlay': underlay}))
    store = package_store(manager)
    source = await asyncio.to_thread(store.capture, records[0], platform)
    await store.validate_plan(plan, source)
    return await journal.create(plan, source)


async def operation(manager, action='list', group_id='', config=None):
    if action not in {'list', 'inspect', 'create', 'advance', 'stop', 'continue_stop'}:
        raise ValueError('Unsupported model group deployment action')
    if not manager.resolver:
        raise RuntimeError('Fleet is not connected')
    await manager.resolver._ensure_client()
    journal = CreationJournal(manager.client, manager.resolver._fleet)
    if action == 'list':
        return {'creations': await journal.list()}
    if action == 'inspect':
        row = await journal.load(group_id)
        group = await HubGroupJournal(manager.client, journal.owner).load(group_id) if row['phase'] == 'handed_off' else None
        return dict(creation=row, group=group)
    journal.path(group_id)
    # Namespace locks separately while retaining all 64 valid ID characters.
    lock_id = 'group-' + hashlib.sha256(group_id.encode()).hexdigest()[:58]
    async with manager.lock(lock_id):
        if action == 'create':
            return dict(creation=await create(manager, journal, group_id, config), group=None)
        lifecycle = FleetLifecycle(manager.resolver)
        row = await journal.load(group_id)
        builder = package_store(manager) if action == 'advance' else None
        controller = CreationCoordinator(journal, lifecycle, builder=builder)
        if action == 'stop':
            row = await controller.stop(group_id)
        if row['phase'] == 'handed_off':
            group_journal = HubGroupJournal(manager.client, journal.owner)
            group = await group_journal.load(group_id)
            if action == 'continue_stop' and group['phase'] not in {'aborting', 'stopped'}:
                raise ValueError('Stop the group before continuing cleanup')
            group = await GroupCoordinator(group_journal, lifecycle, packages=builder).advance(group_id)
            return dict(creation=row, group=group)
        if action == 'continue_stop' and row['phase'] not in {'aborting', 'stopped'}:
            raise ValueError('Stop the deployment before continuing cleanup')
        if row['phase'] == 'built' and action == 'advance':
            return await journal.handoff(group_id)
        return dict(creation=await controller.advance(group_id), group=None)
