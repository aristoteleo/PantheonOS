"""User-wide backend defaults and immutable per-window Fleet bindings."""
from __future__ import annotations
import asyncio
import json
import re
import time
import uuid
from pathlib import Path

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.apps.portable import portable_backend, stream_backend


class AppPlacement:
    def __init__(self, manager, resolver):
        self.manager, self.resolver = manager, resolver

    def _path(self, app_id):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', app_id or ''):
            raise ValueError('Invalid App id')
        return self.manager.records / 'placements' / f'{app_id}.json'

    def defaults(self):
        root = self.manager.records / 'placements'
        return {p.stem: json.loads(p.read_text()) for p in root.glob('*.json')}

    def default(self, app_id):
        path = self._path(app_id)
        return json.loads(path.read_text()).get('node_id') if path.exists() else None

    def save(self, app_id, node_id):
        path = self._path(app_id)
        with self.manager.lock():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            tmp.write_text(json.dumps({'node_id': node_id or None}))
            tmp.replace(path)
        return {'node_id': node_id or None}

    def resolve(self, app_id, revision=None):
        revision = revision or self.manager.versions.launch_default(app_id)
        if revision:
            resolved = self.manager.versions.resolve(app_id, revision.get('scope', ''),
                revision.get('commit') or 'latest', revision.get('repository_id', ''))
            directory = Path(resolved['dir'])
            revision = resolved.get('revision', revision)
        else:
            app = next((a for a in self.manager.inventory(match_id=app_id)['apps'] if a.get('effective')), None)
            if app is None:
                raise ValueError('App is not in the installed library')
            directory = Path(app['dir'])
        path = next((directory / n for n in ('app.json', 'atrium.json') if (directory / n).is_file()), None)
        if path is None:
            raise ValueError('App manifest is missing')
        manifest = json.loads(path.read_text())
        if manifest['id'] != app_id:
            raise ValueError('App revision identity mismatch')
        return directory, manifest, revision

    async def nodes(self):
        if self.resolver is None:
            raise RuntimeError('Fleet is not connected')
        await self.resolver._ensure_client()
        from pantheon.apps.builtin.fleet.inventory import node_inventory
        return node_inventory(await self.resolver._list_nodes(max_age=0))['nodes']

    @staticmethod
    def incompatibility(node, manifest):
        if node['status'] not in ('online', 'busy'):
            return 'Node is offline'
        runtimes = node.get('runtimes', {})
        if stream_backend(manifest):
            tools = set(node.get('tools') or [])
            if node.get('os') == 'linux':
                if not {'xpra', 'Xvfb', 'xdpyinfo'} <= tools:
                    return 'Install Xpra, Xvfb and x11-utils, then restart/update Fleet to report them'
            elif node.get('os') in ('darwin', 'windows'):
                if runtimes.get('native-capture') != '1':
                    return 'Update Fleet with its native capture helper; an interactive desktop is required'
                if runtimes.get('native-capture-ready') != '1':
                    return 'Run fleet capture permissions on this node, allow recording and input, then restart Fleet'
            else:
                return 'Streaming supports Linux, macOS and Windows nodes'
            if manifest['id'] == 'qupath' and 'qupath' not in tools:
                return 'Install QuPath on this node before starting this app'

        if runtimes.get('app-lifecycle') != '1' or runtimes.get('app-services') != '1':
            return 'Update Fleet to enable managed App services'
        execution = manifest.get('execution') or {}
        if not execution and not portable_backend(manifest):
            return 'This App has no portable backend package'
        if portable_backend(manifest) and runtimes.get('app-rpc') != '1':
            return 'Update Fleet to enable portable App backends'
        variants = execution.get('platform_manifests') or {}
        if variants and f"{node.get('os')}-{node.get('arch')}" not in variants:
            return 'App does not support this operating system / architecture'
        if portable_backend(manifest) and (node.get('os') not in ('linux', 'darwin', 'windows') or node.get('arch') not in ('amd64', 'arm64')):
            return 'Unsupported backend platform'
        if portable_backend(manifest) and 'proc' not in node.get('caps', []):
            return 'Node does not allow process execution'
        return ''

    async def target(self, app_id, manifest, node_id=None):
        selected = node_id or self.default(app_id)
        nodes = await self.nodes()
        fallback_reason = ''
        if selected:
            node = next((n for n in nodes if n['node_id'] == selected), None)
            reason = self.incompatibility(node, manifest) if node else 'Node is no longer in your Fleet'
            if not reason:
                return node
            if node_id:
                raise ValueError(f"{node['name'] if node else node_id}: {reason}. Choose another node in Fleet or reconnect this node.")
            fallback_reason = f"{node['name'] if node else selected}: {reason}"
        eligible = [n for n in nodes if not self.incompatibility(n, manifest)]
        eligible.sort(key=lambda n: (n.get('kind') != 'sandbox', n['node_id']))
        if not eligible:
            raise RuntimeError('No compatible backend node is online. Update Fleet or configure an eligible node in Fleet.')
        return {**eligible[0], **({'preferred_node_id': selected, 'fallback_reason': fallback_reason} if fallback_reason else {})}

    async def install(self, app_id, node_id=None, revision=None, operation_id=None):
        timings = {}
        started = time.monotonic()
        def mark(stage):
            nonlocal started
            now = time.monotonic()
            timings[stage] = round((now - started) * 1000)
            started = now
        directory, manifest, revision = await asyncio.to_thread(self.resolve, app_id, revision)
        mark('resolve')
        node = await self.target(app_id, manifest, node_id)
        mark('placement')
        lifecycle = FleetLifecycle(self.resolver)
        digest = await lifecycle.stage(node['node_id'], directory,
            immutable_revision=(revision or {}).get('commit'))
        mark('package')
        with self.manager.lock():
            records = self.manager.records / 'node-artifacts'
            records.mkdir(parents=True, exist_ok=True)
            path = records / f'{digest}.json'
            tmp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            tmp.write_text(json.dumps({'app_id': app_id, 'revision': revision, 'manifest': manifest}))
            tmp.replace(path)
        operation = await lifecycle.submit(node['node_id'], 'install', digest, operation_id=operation_id)
        mark('submit')
        return {'success': True, 'node_id': node['node_id'], 'node_name': node['name'],
                'digest': digest, 'operation': operation, 'app_revision': revision,
                'component': 'backend' if portable_backend(manifest) else app_id,
                'preferred_node_id': node.get('preferred_node_id'), 'fallback_reason': node.get('fallback_reason'),
                'timings_ms': timings}

    async def ensure(self, app_id, node_id=None, revision=None, timeout=600):
        started = await self.install(app_id, node_id, revision)
        lifecycle = FleetLifecycle(self.resolver)
        deadline = time.monotonic() + timeout
        async def wait(operation):
            while time.monotonic() < deadline:
                snapshot = await lifecycle.status(started['node_id'])
                op = snapshot['operations'].get(operation['request']['operation_id'])
                if not op:
                    raise RuntimeError('App operation disappeared; check Fleet before retrying')
                if op['state'] == 'succeeded':
                    return snapshot
                if op['state'] not in ('queued', 'running'):
                    raise RuntimeError(op.get('error') or f"App operation {op['state']}")
                await asyncio.sleep(.5)
            raise TimeoutError('App setup continues on the node. Check progress in Fleet before retrying.')
        state = await wait(started['operation'])
        def instance():
            return next((i for i in state['instances'].values() if i['digest'] == started['digest'] and i['scope'] == 'app'), None)
        current = instance()
        if not current or current['state'] == 'stopped':
            op = await lifecycle.submit(started['node_id'], 'start', started['digest'], generation=(current or {}).get('generation', 0))
            try:
                state = await wait(op)
            except RuntimeError:
                # A concurrent viewport may have won the same cold start. Re-read
                # the exact artifact; do not replay the lifecycle operation.
                state = await lifecycle.status(started['node_id'])
                if not instance() or instance()['state'] != 'ready':
                    raise
            current = instance()
        elif current['state'] == 'starting':
            op = next((o for o in state['operations'].values() if o['request']['action'] == 'start'
                and o['request']['digest'] == started['digest'] and o['state'] in ('queued', 'running')), None)
            if op:
                state = await wait(op)
                current = instance()
        if not current or current['state'] != 'ready':
            raise RuntimeError('This App requires recovery in Fleet; its existing instance was not restarted')
        return {k: v for k, v in {'node_id': started['node_id'], 'node_name': started['node_name'],
            'instance_id': current['instance_id'], 'revision': current['digest'],
            'generation': current['generation'], 'component': started['component'], 'port': 'http',
            'app_revision': started['app_revision']}.items() if v is not None}

    async def call(self, app_id, binding, method, args, timeout):
        lifecycle = FleetLifecycle(self.resolver)
        client = await lifecycle._client(binding['node_id'])
        state = await lifecycle.status(binding['node_id'])
        instance = state['instances'].get(binding['instance_id'])
        if not instance or instance['app_id'] != app_id:
            raise ValueError('Backend binding does not belong to this App')
        if instance['digest'] != binding['revision'] or instance['generation'] != binding['generation']:
            raise ValueError('Backend instance changed; reconnect the window through Fleet')
        response = await client.invoke(binding['node_id'], app_id, binding, method, args or {}, timeout)
        if response.get('error'):
            raise RuntimeError(response['error'])
        result = response['response']
        return {**result, 'backend': binding}

    async def describe_binding(self, app_id, binding):
        snapshot = await FleetLifecycle(self.resolver).status(binding['node_id'])
        instance = snapshot['instances'].get(binding['instance_id'])
        if not instance or instance['app_id'] != app_id or instance['digest'] != binding['revision'] or instance['generation'] != binding['generation']:
            raise ValueError('App binding is no longer valid on this node')
        digest = instance['digest']
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Invalid App digest')
        path = self.manager.records / 'node-artifacts' / f'{digest}.json'
        if not path.is_file():
            raise ValueError('App source revision is unavailable. Reinstall this version from your library.')
        record = json.loads(path.read_text())
        if record['app_id'] != app_id:
            raise ValueError('App artifact identity mismatch')
        return record
