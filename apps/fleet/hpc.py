"""HPC compute nodes: a Fleet node on a Slurm login node starts more nodes.

The Agent mints one single-use join token per job with the user's Fleet key;
the login node only ever sees that token. Tokens live long enough to outlast a
queue wait (they are deleted when the job starts or is cancelled).
"""
import asyncio
import os

from .inventory import node_inventory

TOKEN_TTL_MINUTES = 7 * 24 * 60


async def mint_join_token(ttl_minutes: int = TOKEN_TTL_MINUTES) -> str:
    import httpx
    controller = os.environ.get('FLEET_CONTROLLER_URL', '')
    key = os.environ.get('FLEET_KEY') or os.environ.get('PANTHEON_API_KEY') or ''
    if not (controller and key):
        raise RuntimeError('Fleet is not configured on this backend')
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(controller.rstrip('/') + '/join-tokens', json={'key': key, 'ttl_minutes': ttl_minutes})
    r.raise_for_status()
    return r.json()['join_token']


async def _launcher(resolver, node_id: str) -> dict:
    if resolver is None:
        raise RuntimeError('Fleet is not connected')
    await resolver._ensure_client()
    nodes = node_inventory(await resolver._list_nodes(max_age=2))['nodes']
    node = next((n for n in nodes if n['node_id'] == node_id), None)
    if node is None:
        raise ValueError('Node is not in this user’s Fleet')
    if node['status'] not in ('online', 'busy'):
        raise RuntimeError('The login node is offline')
    if node.get('runtimes', {}).get('slurm-launcher') != '1':
        raise RuntimeError('This node cannot start HPC compute nodes: run Fleet (0.5.0-model.9 or later) on a '
                           'Slurm login node')
    return node


async def call(resolver, node_id: str, method: str, **data) -> dict:
    await _launcher(resolver, node_id)
    reply = await resolver._client.hpc(node_id, method, data)
    if reply.get('error'):
        raise RuntimeError(str(reply['error']))
    return reply


async def launch(resolver, node_id: str, *, partition: str, cpus: int = 4, mem_gb: int = 16, minutes: int = 240,
                 gpus: int = 0, gpu_type: str = '', count: int = 1, name: str = '', account: str = '',
                 qos: str = '') -> dict:
    """Submit `count` single-node jobs; each joins the Fleet as its own node."""
    node = await _launcher(resolver, node_id)
    if not 1 <= count <= 16:
        raise ValueError('count must be 1-16')
    base = name or (partition + ('-gpu' if gpus else ''))

    async def one(_):
        request = {'join_token': await mint_join_token(), 'name': base, 'partition': partition, 'cpus': cpus,
                   'mem_gb': mem_gb, 'minutes': minutes, 'gpus': gpus, 'gpu_type': gpu_type,
                   'account': account, 'qos': qos}
        reply = await resolver._client.hpc(node_id, 'submit', {'request': request})
        if reply.get('error'):
            raise RuntimeError(str(reply['error']))
        return reply['job']

    jobs = await asyncio.gather(*(one(i) for i in range(count)))
    return {'success': True, 'launcher': node['name'], 'jobs': list(jobs)}


# --- Clusters reached through a signed-in session (sites that forbid Fleet on them) ---

CLUSTER_ACTIONS = {'list', 'save', 'remove', 'sign_in', 'status', 'answer', 'sign_out', 'touch',
                   'partitions', 'jobs', 'submit', 'cancel'}
# The Agent never handles sign-in: prompts and answers stay between the user and the node.
AGENT_CLUSTER_ACTIONS = {'list', 'status', 'partitions', 'jobs', 'submit', 'cancel'}


async def cluster(resolver, node_id: str, action: str, **data) -> dict:
    if action not in CLUSTER_ACTIONS:
        raise ValueError('unknown action')
    if resolver is None:
        raise RuntimeError('Fleet is not connected')
    await resolver._ensure_client()
    nodes = node_inventory(await resolver._list_nodes(max_age=2))['nodes']
    node = next((n for n in nodes if n['node_id'] == node_id), None)
    if node is None:
        raise ValueError('Node is not in this user’s Fleet')
    if node['status'] not in ('online', 'busy'):
        raise RuntimeError('The connecting node is offline')
    if node.get('runtimes', {}).get('hpc-connector') != '1':
        raise RuntimeError('Update Fleet on this machine (0.5.0-model.10 or later) to connect HPC clusters')
    reply = await resolver._client.hpc_cluster(node_id, action, data)
    if reply.get('error'):
        raise RuntimeError(str(reply['error']))
    return reply


async def service(resolver, node_id: str, action: str = 'list', **data) -> dict:
    """Start/inspect/stop one managed HTTP process inside an existing allocation."""
    if action not in {'list', 'start', 'stop'}:
        raise ValueError('action must be list, start or stop')
    if resolver is None:
        raise RuntimeError('Fleet is not connected')
    await resolver._ensure_client()
    nodes = node_inventory(await resolver._list_nodes(max_age=2))['nodes']
    node = next((n for n in nodes if n['node_id'] == node_id), None)
    if not node or node.get('runtimes', {}).get('hpc-services') != '1':
        raise ValueError('Choose an HPC compute node with service support')
    if action != 'list' and node['status'] not in ('online', 'busy'):
        raise RuntimeError('HPC compute node is unavailable')
    reply = await resolver._client.hpc_service(node_id, action, **data)
    if reply.get('error'):
        raise RuntimeError(str(reply['error']))
    return reply
