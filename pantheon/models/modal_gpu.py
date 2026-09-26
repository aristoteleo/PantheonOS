"""Platform Modal GPU model services: one GPU node, one managed catalog LLM, one route.

The Hub launches the node on the platform's Modal account with a one-use join
token that this Agent mints with the user's own fleet key, so the Hub never sees
that key. Everything after the node joins uses ordinary Model Services: a
managed deployment of the node-provided SGLang serving a pinned catalog model,
automatic publication with the catalog's capabilities, and a stable route
`fleet-route://<service_id>` that follows each new launch (new node, new GPU).

`advance` is idempotent: it observes Hub, Fleet and Model Services and takes at
most the next step, so the UI (or an Agent tool) can call it repeatedly.
"""
import asyncio
import os
import re

import httpx

RECIPE = 'sglang-0.5.20-linux-amd64-node'
GPUS = {'H100', 'H200', 'B200', 'A100-80GB', 'L40S'}
GPU_COUNTS = (1, 2, 4, 8)  # one machine; tensor parallel needs a power of two
# Bare Fleet nodes may also be CPU-only ('none'); model services need a GPU.
NODE_GPUS = GPUS | {'none'}
NODE_CPU = [2, 4, 8, 16, 32, 64]
NODE_MEMORY_GIB = [8, 16, 32, 64, 128, 256, 512]
SERVICE_ID = r'[a-z0-9][a-z0-9-]{0,40}'
_starts = {}  # deployment_id -> background engine start task (this Agent process only)


def deployment_id(service_id):
    return 'modal-' + service_id


def _check(service_id):
    if not isinstance(service_id, str) or not re.fullmatch(SERVICE_ID, service_id):
        raise ValueError('Service id must be lowercase letters, digits and dashes')


def _catalog(model_id):
    from .managed import module
    return module('llm_models').model(model_id)


async def _controller(path, body):
    controller = os.environ.get('FLEET_CONTROLLER_URL', '')
    key = os.environ.get('FLEET_KEY') or os.environ.get('PANTHEON_API_KEY') or ''
    if not (controller and key):
        raise RuntimeError('Fleet is not configured on this Agent')
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(controller.rstrip('/') + path, json={'key': key, **body})
    if response.status_code != 200:
        raise RuntimeError(f'Fleet controller refused {path} ({response.status_code})')
    return response.json()


async def services(manager):
    return (await manager.client.hub_request('GET', '/api/model-services/modal-gpu'))['services']


async def settle_expired(manager, launches=None, nodes=None, rows=None):
    """Mark Modal model services stopped once their launch ended (lifetime or stop).

    The GPU sandbox is gone, so its node never returns; without this the row stays
    'ready' on a missing node and every consumer that probes it fails. The node
    identity is revoked so it can never act on the old deployment.
    """
    launches = launches if launches is not None else await services(manager)
    live = {launch['service_id'] for launch in launches}
    nodes = nodes if nodes is not None else await manager.resolver._list_nodes(max_age=0)
    online = {n['node_id'] for n in nodes}
    rows = rows if rows is not None else await manager.client.deployments()
    settled = []
    for row in rows:
        service_id = row['deployment_id'].removeprefix('modal-')
        if (not row['deployment_id'].startswith('modal-') or service_id in live
                or row['state'] in {'stopped', 'draft'} or row['node_id'] in online):
            continue
        try:
            await _controller('/revoke', {'node_id': row['node_id']})
        except Exception:
            pass  # Already revoked or unknown: the sandbox is gone either way.
        await manager.client.save({**row, 'state': 'stopped'})
        settled.append(row['deployment_id'])
    return settled


def _node_for(nodes, service_id):
    label = 'svc-' + service_id
    matches = [n for n in nodes if label in (n.get('labels') or [])
               and (n.get('state') or {}).get('status', 'online') == 'online']
    return max(matches, key=lambda n: n.get('last_seen') or '') if matches else None


def _gpus(node):
    """Every NVIDIA GPU of the node; a model service leases all of them (tensor parallel)."""
    accelerators = (((node.get('capability') or {}).get('resources') or {}).get('accelerators') or [])
    cuda = sorted((a for a in accelerators if a.get('backend') == 'cuda' and str(a.get('id', '')).startswith('GPU-')),
                  key=lambda a: a['id'])
    if len(cuda) not in GPU_COUNTS:
        raise RuntimeError('The Modal GPU node must report 1, 2, 4 or 8 NVIDIA GPUs')
    return cuda


def _launch_body(service_id, gpu, count, token, lifetime_minutes, sizes=None):
    return dict(service_id=service_id, gpu=gpu, join_token=token, lifetime_minutes=lifetime_minutes,
                **({'gpu_count': count} if count != 1 else {}), **(sizes or {}))


def machine_label(gpu, count=1):
    return gpu if count == 1 else f'{count}× {gpu}'


def _gpu_mismatch(selected, gpu_name):
    """Message when this GPU cannot run the model (e.g. FP8 kernels need H100/L40S)."""
    supported = selected.get('supported_gpus') or sorted(GPUS)
    if any(g.replace('-', ' ').split()[0].lower() in str(gpu_name).lower() for g in supported):
        return ''
    return f"{selected['display_name']} needs {' or '.join(supported)}; this node has {gpu_name}"


async def start(manager, service_id, model_id='qwen3.6-35b-a3b-fp8', gpu='H100', lifetime_minutes=240, gpu_count=1):
    """Launch the GPU node; later `advance` calls prepare, start and publish the model."""
    _check(service_id)
    selected = _catalog(model_id)
    if gpu not in GPUS:
        raise ValueError('Choose ' + ', '.join(sorted(GPUS)))
    if gpu_count not in GPU_COUNTS:
        raise ValueError('A machine has 1, 2, 4 or 8 GPUs')
    if message := _gpu_mismatch(selected, gpu):
        raise ValueError(message)
    if not any(s['service_id'] == service_id for s in await services(manager)):
        token = (await _controller('/join-tokens', {}))['join_token']
        try:
            await manager.client.hub_request('POST', '/api/model-services/modal-gpu',
                                             _launch_body(service_id, gpu, gpu_count, token, lifetime_minutes))
        finally:
            del token
    return await advance(manager, service_id, model_id)


async def _retire(manager, row, nodes):
    """A previous launch's deployment whose node is gone: revoke that node, then forget the row."""
    if any(n['node_id'] == row['node_id'] for n in nodes):
        raise RuntimeError('The previous GPU node is still online; stop it before starting again')
    await _controller('/revoke', {'node_id': row['node_id']})
    for route in await manager.client.routes():
        if any(c['deployment_id'] == row['deployment_id'] for c in route['candidates']):
            await manager.client.route_operation('delete', route_id=route['route_id'], revision=route['revision'])
    if row['state'] not in {'stopped', 'draft'}:
        row = await manager.client.save({**row, 'state': 'stopped'})
    await manager.client.remove(row['deployment_id'], row['revision'])


async def advance(manager, service_id, model_id='qwen3.6-35b-a3b-fp8', model=None, context_length=None):
    """Advance a model service on the Modal node svc-<service_id>.

    `model` is an explicit custom entry (pinned from Hugging Face); otherwise
    `model_id` names a catalog model.
    """
    _check(service_id)
    selected = _catalog(model if model is not None else model_id)
    model_id = selected['id']
    custom = {'model_manifest': selected} if model is not None else {}
    dep = deployment_id(service_id)
    launched = next((s for s in await services(manager) if s['service_id'] == service_id), None)
    nodes = await manager.resolver._list_nodes(max_age=0)
    node = _node_for(nodes, service_id)
    rows = {r['deployment_id']: r for r in await manager.client.deployments()}
    row = rows.get(dep)
    base = dict(service_id=service_id, model_id=model_id, gpu=(launched or {}).get('gpu'),
                gpu_count=(launched or {}).get('gpu_count') or 1,
                expires_at=(launched or {}).get('expires_at'), route=f'fleet-route://{service_id}')
    if not launched:
        await settle_expired(manager, [], nodes, [row] if row else [])
        return dict(base, phase='stopped', ready=False)
    if not node:
        return dict(base, phase='starting_node', ready=False)
    if row and row['node_id'] != node['node_id']:
        await _retire(manager, row, nodes)
        row = None
    if row is None:
        gpus = _gpus(node)
        if message := _gpu_mismatch(selected, gpus[0].get('name') or (launched or {}).get('gpu', '')):
            return dict(base, phase='failed', ready=False, error=message)
        from .managed import module
        tp = len(gpus)
        need = module('llm_models').rank_gpu_bytes(selected, tp)
        if any(g['memory']['total_bytes'] * 9 // 10 < need for g in gpus):
            return dict(base, phase='failed', ready=False,
                        error=f"{selected['display_name']} needs about {need / (1 << 30):.0f} GB on each of "
                              f"{tp} GPU(s); choose more or larger GPUs")
        # The owner may choose a longer context up to the model's maximum (agents carry
        # tens of thousands of tokens of instructions and tool definitions).
        context = int(context_length or selected['context_length'])
        if not 512 <= context <= selected['maximum_context_length']:
            return dict(base, phase='failed', ready=False,
                        error=f"Context must be between 512 and {selected['maximum_context_length']:,} tokens")
        config = dict(recipe_id=RECIPE, model_recipe_id=model_id, context_length=context,
                      parallel=4, keep_alive_seconds=0, load_policy='resident', **custom,
                      **({'tensor_parallel_size': tp} if tp > 1 else {}),
                      resources=dict(memory_bytes=selected['minimum_memory_bytes'], devices=[dict(
                          id=g['id'], backend='cuda', memory_bytes=g['memory']['total_bytes'] * 9 // 10, exclusive=True)
                          for g in gpus]))
        row = await manager.create_managed(
            dep, f"{selected['display_name']} on Modal {machine_label(base['gpu'], tp)}", node['node_id'], config)
    if row['state'] == 'ready':
        return await _publish(manager, row, selected, node, base)
    manifest = {'manifest': row['managed']['model_manifest']} if row['managed'].get('model_manifest') else {}
    weights = await manager.rpc(row['binding'], 'llm_models', dict(action='status', model_id=model_id, **manifest))
    if not weights['ready']:
        jobs = (await manager.rpc(row['binding'], 'llm_models', dict(action='jobs')))['jobs']
        job = next((j for j in jobs if j.get('job_id') == model_id), None)
        if not job or job.get('state') in {'failed', 'cancelled'}:
            await manager.rpc(row['binding'], 'llm_models', dict(action='prepare', model_id=model_id, resume=True, **manifest))
        return dict(base, phase='downloading_weights', ready=False,
                    progress=dict(bytes=(job or {}).get('bytes_done', 0), total=sum(f['size'] for f in selected['files']),
                                  state=(job or {}).get('state', 'queued'), error=(job or {}).get('error', '')))
    task = _starts.get(dep)
    if task is None or task.done():
        if task is not None and task.exception():
            _starts.pop(dep)
            return dict(base, phase='failed', ready=False, error=str(task.exception())[:300])
        _starts[dep] = asyncio.create_task(manager.set_running(dep, True))
    return dict(base, phase='starting_engine', ready=False)


async def _publish(manager, row, selected, node, base):
    from .managed import module
    model = module('llm_models').served_name(selected)
    if not any(m['id'] == model for m in row.get('models') or []):
        caps = selected['capabilities']
        row = await manager.publish(row['deployment_id'], [dict(
            id=model, name=selected['display_name'], operations=['text'], tools=caps['tools'],
            vision=caps['vision'], reasoning=caps['reasoning'], structured_output=True,
            context=row['managed']['context_length'], compute='node')], row['revision'])
    routes = {r['route_id']: r for r in await manager.client.routes()}
    route = routes.get(base['service_id'])
    candidate = dict(deployment_id=row['deployment_id'], model_id=model)
    if not route or route['candidates'] != [candidate] or route['allowed_nodes'] != [node['node_id']]:
        await manager.client.route_operation('save', route=dict(
            route_id=base['service_id'], name=selected['display_name'] + ' (Modal GPU)',
            candidates=[candidate], allowed_nodes=[node['node_id']],
            requires=dict(operation='text', tools=selected['capabilities']['tools'],
                          context=row['managed']['context_length']),
            revision=route['revision'] if route else 0))
    return dict(base, phase='ready', ready=True, node_id=node['node_id'],
                model=f"fleet-model://{row['deployment_id']}/{model}")


async def stop(manager, service_id):
    """Stop the engine while the node is alive, then end the GPU sandbox and revoke the node."""
    _check(service_id)
    dep = deployment_id(service_id)
    rows = {r['deployment_id']: r for r in await manager.client.deployments()}
    node = _node_for(await manager.resolver._list_nodes(max_age=0), service_id)
    if dep in rows and rows[dep]['state'] not in {'stopped', 'draft'} and node:
        await manager.set_running(dep, False)
    await manager.client.hub_request('DELETE', f'/api/model-services/modal-gpu/{service_id}')
    if node:
        await _controller('/revoke', {'node_id': node['node_id']})
    return dict(service_id=service_id, phase='stopped', ready=False)


def node_service_id(hint):
    """A bare Fleet app node reuses the service launcher under a `node-` id."""
    service_id = 'node-' + re.sub('[^a-z0-9-]+', '-', str(hint).lower()).strip('-')[:35]
    _check(service_id)
    return service_id


async def start_node(manager, node_id_hint, gpu='H100', lifetime_minutes=240, cpu=None, memory_gib=None, gpu_count=1):
    """Launch a bare Modal Fleet node (no model), with 1-8 GPUs or CPU-only ('none'); it joins the user's Fleet."""
    service_id = node_service_id(node_id_hint)
    if gpu not in NODE_GPUS:
        raise ValueError('Choose ' + ', '.join(sorted(GPUS)) + ' or none (CPU only)')
    if gpu_count not in GPU_COUNTS or (gpu == 'none' and gpu_count != 1):
        raise ValueError('A machine has 1, 2, 4 or 8 GPUs')
    sizes = {k: v for k, v in (('cpu', cpu), ('memory_gib', memory_gib)) if v is not None}
    if any(type(v) is not int for v in sizes.values()):
        raise ValueError('CPU cores and memory must be whole numbers')
    if not any(s['service_id'] == service_id for s in await services(manager)):
        token = (await _controller('/join-tokens', {}))['join_token']
        try:
            await manager.client.hub_request('POST', '/api/model-services/modal-gpu',
                                             _launch_body(service_id, gpu, gpu_count, token, lifetime_minutes, sizes))
        finally:
            del token
    node = _node_for(await manager.resolver._list_nodes(max_age=0), service_id)
    return dict(service_id=service_id, gpu=gpu, gpu_count=gpu_count, phase='ready' if node else 'starting_node',
                node_id=(node or {}).get('node_id'))


async def stop_node(manager, service_id):
    """End a bare Modal node's sandbox and revoke the node if it is online."""
    _check(service_id)
    if not service_id.startswith('node-'):
        raise ValueError('This Modal GPU node runs a model service; stop it from Model Services')
    node = _node_for(await manager.resolver._list_nodes(max_age=0), service_id)
    await manager.client.hub_request('DELETE', f'/api/model-services/modal-gpu/{service_id}')
    if node:
        await _controller('/revoke', {'node_id': node['node_id']})
    return dict(service_id=service_id, phase='stopped', ready=False)
