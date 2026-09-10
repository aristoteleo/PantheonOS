"""A credential-free view of the current user's Fleet for Files and Fleet UI."""
from datetime import datetime, timezone


def node_inventory(records: list[dict]) -> dict:
    nodes, instances = [], []
    now = datetime.now(timezone.utc)
    for record in records:
        cap, state = record.get('capability') or {}, record.get('state') or {}
        status = state.get('status') or 'unknown'
        try:
            seen = datetime.fromisoformat(record['last_seen'].replace('Z', '+00:00'))
            if (now - seen).total_seconds() > 90:
                status = 'offline'
        except (KeyError, ValueError, TypeError):
            status = 'unknown'
        apps = state.get('instances') or []
        has_files = bool({'fs:workspace', 'fs:local'} & set(cap.get('caps') or [])) or any(
            app.get('app_id') in ('file-manager', 'node-files') for app in apps)
        node = {key: record.get(key) for key in ('node_id', 'name', 'kind', 'last_seen', 'version')}
        node['name'] = node['name'] or node['node_id']
        node.update(status=status, has_files=has_files, os=cap.get('os'), arch=cap.get('arch'),
                    cpu_cores=cap.get('cpu_cores'), ram_gb=cap.get('ram_gb'),
                    disk_free_gb=cap.get('disk_free_gb'), gpu=cap.get('gpu'),
                    load=state.get('load') or {}, caps=cap.get('caps') or [],
                    file_roots=cap.get('file_roots') or [])
        nodes.append(node)
        for app in apps:
            item = {key: app.get(key) for key in ('app_id', 'scope', 'version', 'service_id', 'health')}
            item.update(node_id=node['node_id'], node_name=node['name'], node_status=status, kind='service')
            instances.append(item)
    return {'success': True, 'nodes': nodes, 'instances': instances,
            'observed_at': now.isoformat()}


async def fleet_inventory(resolver) -> dict:
    if resolver is None:
        raise RuntimeError('Fleet is not connected')
    await resolver._ensure_client()
    return await inventory_from_records(await resolver._list_nodes(max_age=5))


async def inventory_from_records(records: list[dict]) -> dict:
    result = node_inventory(records)
    import asyncio
    from pantheon.apps.proxy import ToolsetProxy
    async def desktop_apps(instance):
        try:
            async with asyncio.timeout(4):
                reply = await ToolsetProxy.from_toolset(instance['service_id']).invoke('fleet_instances', {})
            if not reply.get('success'):
                raise RuntimeError(reply.get('error', 'Desktop inventory unavailable'))
            return [{**app, 'node_id': instance['node_id'], 'node_name': instance['node_name'],
                     'node_status': instance['node_status']} for app in reply.get('instances', [])]
        except Exception as exc:
            result.setdefault('warnings', []).append({'node_id': instance['node_id'], 'error': str(exc) or 'Desktop inventory timed out'})
            return []
    desktops = [i for i in result['instances'] if i['app_id'] == 'desktop'
                and i['node_status'] in ('online', 'busy') and i['health'] == 'healthy' and i['service_id']]
    for apps in await asyncio.gather(*(desktop_apps(i) for i in desktops)):
        result['instances'].extend(apps)
    return result
