"""Compile explicit SGLang rank launches; no scheduling or implicit node choices.

Internal distributed launch path. The ordinary managed service API still rejects
group fields until coordinated install, credentials and publication are wired.
All ranks must authenticate the returned topology before loading the model.
"""
from copy import deepcopy
import hashlib
import ipaddress
import json
import re
from urllib.request import urlopen

from group_network import PeerTopology, addresses as validate_underlay
import sglang_runtime


CONTAINER_RECIPE = 'sglang-0.5.20-linux-amd64'
# Same pinned SGLang, run as a Fleet process over a provider-private network
# (e.g. Modal i6pn) where containers or kernel WireGuard are unavailable.
PROCESS_RECIPE = 'sglang-0.5.20-linux-amd64-process'
PLATFORM_PRIVATE = 'platform-private'


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _validate(plan, record):
    if not isinstance(plan, dict) or set(plan) - {'underlay', 'inference_protocol', 'network_mode'} != {
            'protocol', 'owner', 'group_id', 'recipe_id', 'model_sha256',
            'tensor_parallel_size', 'context_length', 'parallel', 'rendezvous_port', 'members'}:
        raise ValueError('Declare a complete immutable SGLang group plan')
    platform = 'network_mode' in plan
    if platform and (plan['network_mode'] != PLATFORM_PRIVATE or 'underlay' in plan):
        raise ValueError('A platform-private group uses the provider network, not a Fleet underlay')
    if 'inference_protocol' in plan and (type(plan['inference_protocol']) is not int or plan['inference_protocol'] != 1
                                         or not ('underlay' in plan or platform)):
        raise ValueError('Group inference requires protocol 1 and the managed private network')
    tp, members = plan['tensor_parallel_size'], plan['members']
    if (plan['recipe_id'] != (PROCESS_RECIPE if platform else CONTAINER_RECIPE)
            or type(tp) is not int or tp not in {2, 4, 8}
            or not isinstance(members, list) or not 2 <= len(members) <= tp
            or tp % len(members) != 0
            or not _integer(plan['context_length'], 512, 1048576)
            or not _integer(plan['parallel'], 1, 16)
            or not _integer(plan['rendezvous_port'], 1024, 65535)):
        raise ValueError('This recipe requires uniform node-local TP and an explicit private rendezvous')
    if 'underlay' in plan:
        validate_underlay(plan['underlay'], len(members))
        if any(m.get('interface') != 'wg0' for m in members if isinstance(m, dict)):
            raise ValueError('Managed private groups require their owned wg0 interface')
    peers, configs, capacities, devices, addresses, families = [], [], [], set(), set(), set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {
                'rank', 'node_id', 'generation', 'address', 'control_port',
                'interface', 'resources', 'physical_gpu_bytes'}:
            raise ValueError('Pin every rank, private interface, resources and measured GPU capacity')
        if (not isinstance(member['interface'], str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,14}', member['interface'])
                or member['interface'] == 'lo'):
            raise ValueError('Use an exact private network interface, not a wildcard or loopback')
        if platform and (member['interface'] == 'wg0' or not isinstance(member['address'], str)
                         or not ipaddress.ip_address(member['address']) in ipaddress.ip_network('fc00::/7')):
            raise ValueError('Platform-private ranks use the provider IPv6 ULA address and interface')
        resources, totals = member['resources'], member['physical_gpu_bytes']
        if (not isinstance(resources, dict) or set(resources) != {'memory_bytes', 'devices'}
                or not _integer(resources['memory_bytes'], 256 << 20, 1 << 50)
                or not isinstance(resources['devices'], list) or len(resources['devices']) != tp // len(members)
                or not isinstance(totals, list) or len(totals) != len(resources['devices'])
                or any(not _integer(total, 256 << 20, 1 << 50) for total in totals)):
            raise ValueError('Declare the exact node-local GPU count, capacity and system budget')
        for device in resources['devices']:
            if (not isinstance(device, dict) or set(device) != {'id', 'backend', 'memory_bytes', 'exclusive'}
                    or not isinstance(device['id'], str) or not re.fullmatch(r'GPU-[A-Za-z0-9-]{1,124}', device['id'])
                    or device['id'] in devices or device['backend'] != 'cuda' or device['exclusive'] is not True
                    or not _integer(device['memory_bytes'], 256 << 20, 1 << 50)):
                raise ValueError('Each rank needs distinct exclusive NVIDIA GPU UUIDs and explicit budgets')
            devices.add(device['id'])
        peer = {key: member[key] for key in ('rank', 'node_id', 'generation', 'address')}
        peer['port'] = member['control_port']
        peers.append(peer)
    # Validate all network/identity fields before inspecting addresses or sorting.
    peer_document = dict(protocol=plan['protocol'], owner=plan['owner'], group_id=plan['group_id'],
        model_sha256=plan['model_sha256'], launch_sha256='0' * 64, members=peers)
    checked = PeerTopology(peer_document)
    canonical = deepcopy(plan)
    canonical['members'] = sorted(canonical['members'], key=lambda member: member['rank'])
    for member in canonical['members']:
        member['address'] = checked.member(member['rank'])['address']
    launch_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    topology = PeerTopology({**peer_document, 'launch_sha256': launch_hash})
    members = sorted(members, key=lambda member: member['rank'])
    for member in members:
        ip = ipaddress.ip_address(member['address'])
        if str(ip) in addresses:
            raise ValueError('Distributed ranks require distinct private node addresses')
        addresses.add(str(ip))
        families.add(ip.version)
        # Separate control, rendezvous and local HTTP roles; never share a port.
        if member['control_port'] in {plan['rendezvous_port'], 30000} or plan['rendezvous_port'] == 30000:
            raise ValueError('Control, rendezvous and HTTP ports must be distinct')
        config = dict(recipe_id=plan['recipe_id'], model_artifact_sha256=plan['model_sha256'],
            tensor_parallel_size=tp, context_length=plan['context_length'], parallel=plan['parallel'],
            keep_alive_seconds=0, load_policy='resident', resources=deepcopy(member['resources']))
        _, _, limit = sglang_runtime._memory_limits(config, record, member['physical_gpu_bytes'], tp // len(members))
        capacities.append(limit)
        configs.append(config)
    if len(families) != 1:
        raise ValueError('Every collective peer must use the same private address family')
    # One fraction for the complete TP group, not a different KV allocation per
    # node. Each rank still validates the minimum against its own weights/KV.
    fraction = int(min(capacities) * 100000) / 100000
    return members, configs, fraction, topology


def rank_launch(plan, record, rank, measured_gpu_bytes, layout=None):
    """Return argv/env and certificate roster after local-capacity revalidation.

    Container ranks use fixed in-container paths. Process ranks pass their
    node-local layout: weights directory, Fleet-reserved engine port and HOME.
    """
    members, configs, fraction, topology = _validate(plan, record)
    if 'network_mode' in plan:
        if (not isinstance(layout, dict) or set(layout) != {'weights', 'port', 'home'}
                or not all(isinstance(layout[k], str) and layout[k] for k in ('weights', 'home'))
                or not _integer(layout['port'], 1024, 65535)):
            raise ValueError('A process rank needs its node-local weights, engine port and home')
    elif layout is not None:
        raise ValueError('Container ranks use their fixed in-container layout')
    if not _integer(rank, 0, len(members) - 1):
        raise ValueError('Unknown SGLang node rank')
    member = members[rank]
    if (not isinstance(measured_gpu_bytes, list)
            or any(type(total) is not int for total in measured_gpu_bytes)
            or measured_gpu_bytes != member['physical_gpu_bytes']):
        raise ValueError('Local GPU capacity changed since the immutable plan was built')
    # Compile every rank so an impossible heterogeneous member prevents all
    # starts, even when this caller's own memory budget is sufficient.
    commands = [sglang_runtime._launch(config, record, m['physical_gpu_bytes'],
                plan['tensor_parallel_size'] // len(members), fraction)
                for config, m in zip(configs, members)]
    argv = commands[rank]
    argv[argv.index('--host') + 1] = '127.0.0.1'
    if layout:
        argv[argv.index('--model-path') + 1] = layout['weights']
        argv[argv.index('--port') + 1] = str(layout['port'])
    address = topology.member(0)['address']
    rendezvous = f'[{address}]' if ':' in address else address
    argv += ['--nnodes', str(len(members)), '--node-rank', str(rank),
             '--dist-init-addr', f'{rendezvous}:{plan["rendezvous_port"]}']
    # Explicit socket transport over the operator/provider-isolated interface.
    # No automatic RDMA/public-interface selection or inherited engine overrides.
    env = dict(NCCL_SOCKET_IFNAME='=' + member['interface'],
        NCCL_SOCKET_FAMILY='AF_INET6' if ':' in address else 'AF_INET', NCCL_IB_DISABLE='1',
        GLOO_SOCKET_IFNAME=member['interface'], SGLANG_HOST_IP=topology.member(rank)['address'],
        CUDA_VISIBLE_DEVICES=','.join(device['id'] for device in member['resources']['devices']),
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
        SGLANG_DISABLE_UPDATE_CHECK='1', HOME=layout['home'] if layout else '/fleet/state')
    return dict(argv=argv, env=env, topology=topology.document(), node_rank=rank,
                publishable=rank == 0, static_fraction=fraction)


def environment(base, launch):
    """Keep host paths/runtime setup, replacing inherited collective overrides."""
    return {**{key: value for key, value in base.items()
               if not key.startswith(('SGLANG_', 'HF_', 'HUGGING_FACE_', 'NCCL_', 'GLOO_'))
               and key not in {'PANTHEON_APP_RPC_TOKEN', 'CUDA_VISIBLE_DEVICES', 'MASTER_ADDR',
                               'MASTER_PORT', 'WORLD_SIZE', 'RANK', 'LOCAL_RANK', 'HOST_IP'}},
            **launch['env']}


def ready_rank(port, model_sha256, rank):
    """Local readiness only; caller must also check every original group rank."""
    if not _integer(rank, 0, 7) or not _integer(port, 1, 65535):
        raise ValueError('Invalid rank readiness target')
    if rank == 0:
        sglang_runtime.ready(port, model_sha256)
        return 'leader_ready'
    # The pinned SGLang engine opens this dummy server only after worker
    # schedulers are ready. Workers deliberately have no /v1/models endpoint.
    with urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
        if response.status != 200:
            raise ValueError('Worker schedulers are not ready')
    return 'worker_ready'
