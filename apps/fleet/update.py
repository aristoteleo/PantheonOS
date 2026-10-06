"""Fleet updates: tell machine nodes to install a Fleet release and restart."""
import asyncio
import os

from .inventory import node_inventory

ONLINE = ('online', 'busy')


async def latest_release(controller_url: str) -> str:
    """The release tag the Controller rolls out ('' when none is published)."""
    import httpx
    if not controller_url:
        return ''
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(controller_url.rstrip('/') + '/fleet/latest')
    if r.status_code != 200:
        return ''
    return str(r.json().get('tag') or '')


_cached = {'at': 0.0, 'tag': ''}


async def published_release(max_age: float = 600) -> str:
    """latest_release for the configured Controller, cached; '' on any error."""
    import time
    if time.monotonic() - _cached['at'] > max_age:
        try:
            tag = await latest_release(os.environ.get('FLEET_CONTROLLER_URL', ''))
        except Exception:  # noqa: BLE001 — an unreachable Controller just hides updates
            tag = _cached['tag']
        _cached.update(at=time.monotonic(), tag=tag)
    return _cached['tag']


def _outcome(node: dict, tag: str) -> dict:
    return {'node_id': node['node_id'], 'name': node['name'], 'from': node.get('version') or '', 'to': tag}


async def update_nodes(resolver, node_ids: list[str] | None = None, tag: str = '', *, release_lookup=None) -> dict:
    if resolver is None:
        raise RuntimeError('Fleet is not connected')
    if not tag:
        tag = await release_lookup() if release_lookup is not None else await latest_release(os.environ.get('FLEET_CONTROLLER_URL', ''))
    if not tag:
        raise RuntimeError('No Fleet release is published for updates')
    await resolver._ensure_client()
    nodes = node_inventory(await resolver._list_nodes(max_age=2))['nodes']
    wanted = set(node_ids or [])
    if wanted - {n['node_id'] for n in nodes}:
        raise ValueError('Node is not in this user’s Fleet')
    nodes = [n for n in nodes if n['node_id'] in wanted] if wanted else [n for n in nodes if n.get('kind') == 'machine']

    async def one(node):
        out = _outcome(node, tag)
        if node.get('kind') != 'machine':
            return {**out, 'status': 'skipped', 'reason': 'updated with its image, not in place'}
        if node['status'] not in ONLINE:
            return {**out, 'status': 'skipped', 'reason': 'offline'}
        if node.get('runtimes', {}).get('self-update') != '1':
            return {**out, 'status': 'manual', 'reason': 'this Fleet version cannot update itself; reinstall it once'}
        try:
            reply = await resolver._client.self_update(node['node_id'], tag)
        except Exception as exc:  # noqa: BLE001 — per-node result
            return {**out, 'status': 'failed', 'reason': str(exc) or type(exc).__name__}
        if reply.get('error'):
            return {**out, 'status': 'failed', 'reason': str(reply['error'])}
        return {**out, 'status': reply.get('status') or 'failed', 'reason': reply.get('reason') or ''}

    results = await asyncio.gather(*(one(n) for n in nodes))
    return {'success': True, 'tag': tag, 'nodes': list(results)}
