"""The Desktop side of the Agent's PantheonOS interface
(docs/pantheonos-app-interface.md).

The Agent starts, stops, inspects, manages and develops the owner's library
Apps through these operations. Each one reuses the Desktop's own App
management, so the Agent and the UI act the same way. App ids are the
library's; a disabled App is refused.
"""
from __future__ import annotations

import asyncio

# pantheonos_manage_app actions and the Desktop operation each one is.
MANAGE = {
    'search': ('store', 'search'), 'inspect': ('store', 'inspect'), 'install': ('store', 'install'),
    'uninstall': ('library', 'remove'), 'trash': ('library', 'trash'), 'restore': ('library', 'restore'),
    'versions': ('library', 'versions'), 'set_version': ('library', 'default'),
    'set_node': ('placement', 'set'), 'nodes': ('placement', 'options'), 'set_policy': ('placement', 'policy'),
}
# pantheonos_develop_app actions.
DEVELOP = {
    **{a: ('develop', a) for a in ('create', 'status', 'files', 'read', 'write', 'diff', 'branch',
                                    'switch', 'merge', 'commit', 'test')},
    'fork': ('library', 'fork'), 'tag': ('library', 'tag'), 'prepare': ('library', 'prepare'),
    'history': ('history', ''), 'build': ('build', 'start'), 'build_status': ('build', 'status'),
    **{a: ('store', a) for a in ('publish', 'fetch', 'pull', 'community', 'contributions', 'submit',
                                  'inspect_contribution', 'prepare_merge', 'checkout_review', 'review',
                                  'close_contribution')},
    'merge_contribution': ('store', 'merge'),
}
STATUS_TIMEOUT_SECONDS = 10


def _policy(desktop, app_id):
    placement = desktop._app_placement()
    if app_id and placement.policy(app_id) == 'disabled':
        raise ValueError(f'{app_id} is disabled for the Agent in its App settings')
    return placement


async def _node(placement, node):
    """A node of the owner's Fleet by id or name."""
    if not node:
        return None
    for entry in await placement.nodes():
        if node in (entry['node_id'], entry.get('name')):
            return entry
    raise ValueError(f"No node '{node}' in your Fleet")


def _revision(desktop, app_id, version, repository=''):
    """A version (tag, branch:<name>, commit or latest) as a launch revision."""
    if not version:
        return None
    from .store_manager import AppStoreManager
    resolved = AppStoreManager(desktop._app_scope_roots()).versions.resolve(app_id, '', version, repository)
    return resolved.get('revision') or resolved


async def instances(desktop, app_ids=None) -> dict:
    """Running backends of the given Apps (all when None), by App id, read
    from every online node of the Fleet."""
    from pantheon.apps.lifecycle import FleetLifecycle
    placement = desktop._app_placement()
    nodes = [n for n in await placement.nodes() if n['status'] in ('online', 'busy')]
    lifecycle = FleetLifecycle(placement.resolver)

    async def status(node):
        try:
            return node, await asyncio.wait_for(lifecycle.status(node['node_id']), STATUS_TIMEOUT_SECONDS)
        except Exception:
            return node, None  # A node that does not answer has nothing to report.

    found = {}
    for node, snapshot in await asyncio.gather(*(status(n) for n in nodes)):
        for instance in ((snapshot or {}).get('instances') or {}).values():
            app_id = instance.get('app_id')
            if instance.get('scope') != 'app' or instance.get('state') in (None, 'stopped') \
                    or app_ids is not None and app_id not in app_ids:
                continue
            found.setdefault(app_id, []).append({
                'node_id': node['node_id'], 'node': node.get('name'), 'state': instance['state'],
                'instance_id': instance.get('instance_id'), 'revision': instance.get('digest'),
                'generation': instance.get('generation'), 'version': instance.get('version')})
    return found


async def describe(desktop, app_id) -> dict:
    from .agent_apps import offer
    from .store_manager import AppStoreManager
    placement = _policy(desktop, app_id)
    _, manifest, revision = await asyncio.to_thread(placement.resolve, app_id)
    entry = offer(manifest)
    if entry is None:
        raise ValueError(f'{app_id} offers no tools, window actions or window')
    app = await asyncio.to_thread(AppStoreManager(desktop._app_scope_roots()).find, app_id)
    git = app.get('git') or {}
    return {'success': True, **entry, 'policy': placement.policy(app_id),
            'version': manifest.get('version'), 'default_node': placement.default(app_id),
            'launch': revision, 'branch': git.get('branch'), 'branches': git.get('branches'),
            'instances': (await instances(desktop, {app_id})).get(app_id, [])}


async def start(desktop, app_id, *, node='', version='', window=False, job=None, holder='') -> dict:
    placement = _policy(desktop, app_id)
    revision = await asyncio.to_thread(_revision, desktop, app_id, version)
    target = await _node(placement, node)
    result = {'success': True, 'app_id': app_id}
    if job is not None:
        if target is None:
            raise ValueError("A job runs on an HPC cluster's login node: name it in node")
        if not isinstance(job, dict) or not isinstance(job.get('cluster_id'), str):
            raise ValueError('job needs cluster_id and the Slurm resources (name, partition, cpus, mem_gb, minutes)')
        request = {k: v for k, v in job.items() if k != 'cluster_id'}
        return {**result, 'job': await placement.launch_job(app_id, target['node_id'], job['cluster_id'],
                                                            request, revision)}
    _, manifest, _ = await asyncio.to_thread(placement.resolve, app_id, revision)
    if (manifest.get('entry') or {}).get('backend') or manifest.get('execution'):
        binding = await placement.ensure(app_id, target['node_id'] if target else None, revision)
        if holder:
            await desktop._agent_leases().use(binding, holder)
        result['instance'] = binding
    if window:
        opened = await desktop.desktop_open(app=app_id, revision=revision,
                                            node_id=target['node_id'] if target else '')
        if not opened.get('success'):
            return opened
        result['window_id'] = opened.get('window_id') or (opened.get('result') or {}).get('window_id')
    if 'instance' not in result and 'window_id' not in result:
        raise ValueError(f'{app_id} has no backend to start; pass window: true to open it')
    return result


async def stop(desktop, app_id, *, node='', holder='') -> dict:
    from pantheon.apps.lifecycle import FleetLifecycle
    placement = desktop._app_placement()
    target = await _node(placement, node)
    running = [i for i in (await instances(desktop, {app_id})).get(app_id, [])
               if target is None or i['node_id'] == target['node_id']]
    lifecycle = FleetLifecycle(placement.resolver)
    stopped = []
    for instance in running:
        if holder:
            await desktop._agent_leases().release(instance, holder)
        operation = await lifecycle.submit(instance['node_id'], 'stop', instance['revision'],
                                           generation=instance['generation'])
        stopped.append({'node': instance['node'], 'node_id': instance['node_id'],
                        'operation_id': (operation.get('request') or {}).get('operation_id')})
    if not stopped:
        return {'success': True, 'app_id': app_id, 'stopped': [], 'note': f'{app_id} is not running'}
    return {'success': True, 'app_id': app_id, 'stopped': stopped,
            'note': 'The App may ask to finish its work first; a window of it still open starts it again when used.'}


async def _store_install(desktop, repository, version):
    """Install a Store release into the library, as the Store page does."""
    download = await desktop.desktop_app_store('download', repository_id=repository, version=version)
    if download.get('success') is False:
        return download
    return await desktop.desktop_store_manage('install', download=download)


async def manage(desktop, action, app_id='', args=None) -> dict:
    args = dict(args or {})
    if action not in MANAGE:
        raise ValueError(f"Unknown action '{action}'. Actions: {', '.join(MANAGE)}")
    kind, operation = MANAGE[action]
    repository = args.get('repository', '')
    if kind == 'store':
        if operation == 'install':
            if not repository or not args.get('version'):
                raise ValueError('install needs repository and version (from search and inspect)')
            return await _store_install(desktop, repository, args['version'])
        return await desktop.desktop_app_store(operation, query=args.get('query', ''), repository_id=repository)
    if not app_id and operation not in ('trash', 'restore'):
        raise ValueError(f'{action} needs app')
    if kind == 'placement':
        if operation == 'policy' and args.get('policy') not in ('on_demand', 'resident'):
            raise ValueError("set_policy takes on_demand or resident; only the owner disables an App")
        _policy(desktop, app_id)
        node = await _node(desktop._app_placement(), args.get('node', ''))
        return await desktop.desktop_app_placement(operation, app_id=app_id, policy=args.get('policy', ''),
                                                   node_id=node['node_id'] if node else '')
    if app_id:
        _policy(desktop, app_id)
    return await desktop.desktop_store_manage(operation, app_id=app_id, version=args.get('version', ''),
                                              repository_id=repository, archive_id=args.get('archive_id', ''),
                                              scope=args.get('scope', 'user'))


async def develop(desktop, action, app_id, args=None) -> dict:
    args = dict(args or {})
    if action not in DEVELOP:
        raise ValueError(f"Unknown action '{action}'. Actions: {', '.join(DEVELOP)}")
    if not app_id and action not in ('contributions', 'inspect_contribution', 'prepare_merge',
                                     'checkout_review', 'review', 'merge_contribution', 'close_contribution'):
        raise ValueError(f'{action} needs app')
    if app_id and action != 'create':
        _policy(desktop, app_id)
    kind, operation = DEVELOP[action]
    repository, scope = args.get('repository', ''), args.get('scope', '')
    if kind == 'develop':
        return await desktop.desktop_app_develop(
            operation, app_id, repository_id=repository, scope=scope, path=args.get('path', ''),
            files=args.get('files'), branch=args.get('branch', ''), message=args.get('message', ''),
            expected_commit=args.get('expected_commit', ''), command=args.get('command'))
    if kind == 'library':
        return await desktop.desktop_store_manage(operation, app_id=app_id, version=args.get('version', ''),
                                                  repository_id=repository, scope=scope or 'user',
                                                  name=args.get('name', ''))
    if kind == 'history':
        return await desktop.desktop_store_git(app_id, scope=scope or 'user', repository_id=repository)
    if kind == 'build':
        node = await _node(desktop._app_placement(), args.get('node', ''))
        if node is None:
            raise ValueError('build needs node: the platform is that node\'s')
        revision = await asyncio.to_thread(_revision, desktop, app_id, args.get('version', ''), repository)
        return await desktop.desktop_app_build(app_id, node['node_id'], action=operation, revision=revision)
    return await desktop.desktop_app_store(
        operation, app_id=app_id, repository_id=repository, scope=scope or 'user',
        version=args.get('version', ''), name=args.get('name', ''), changelog=args.get('changelog', ''),
        expected_commit=args.get('expected_commit', ''), target_repository_id=args.get('target_repository', ''),
        expected_base=args.get('expected_base', ''), request_id=args.get('request_id', ''),
        title=args.get('title', ''), description=args.get('description', ''),
        decision=args.get('decision', ''), inbox=args.get('inbox', 'all'))
