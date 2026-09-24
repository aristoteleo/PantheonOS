"""Versioned Fleet lifecycle coordinator; execution always belongs to a node.

Artifacts contain a fleet.json declaration and immutable App code. This module
does not run install hooks locally, copy local interpreters, or fall back to a
different node when the requested node cannot run an App.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import tarfile
import uuid
from collections import OrderedDict
from pathlib import Path

PROTOCOL = 1
MAX_ARTIFACT = 32 * 1024 * 1024
CHUNK_SIZE = 192 * 1024


def build_artifact(directory: Path, platform: str | None = None) -> tuple[bytes, str]:
    """Deterministic package. Refuse links and omit mutable/cache/git content."""
    root = directory.resolve(strict=True)
    if not (root / 'fleet.json').is_file():
        raise ValueError('This App has no fleet.json execution declaration')
    manifest_path = next((root / name for name in ('app.json', 'atrium.json') if (root / name).is_file()), None)
    if manifest_path is None:
        raise ValueError('App manifest is missing')
    manifest = json.loads(manifest_path.read_text())
    definition_path = root / 'fleet.json'
    variants = manifest.get('execution', {}).get('platform_manifests', {})
    if variants:
        name = variants.get(platform)
        if not name or not re.fullmatch(r'fleet\.(linux|darwin|windows)-(amd64|arm64)\.json', name):
            raise ValueError(f'This App has no native package for node platform {platform!r}')
        definition_path = root / name
        if definition_path.is_symlink():
            raise ValueError('App artifacts cannot contain symbolic links')
    definition_bytes = definition_path.read_bytes()
    definition = json.loads(definition_bytes)
    if definition.get('protocol') != PROTOCOL or definition.get('app_id') != manifest.get('id') or definition.get('version') != manifest.get('version'):
        raise ValueError('Execution declaration must match the App identity and version')
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as archive:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if any(part in {'.git', '__pycache__', 'node_modules', '.venv'} or part.startswith('.env')
                   for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f'App artifacts cannot contain symbolic links: {relative}')
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f'App artifacts cannot contain special files: {relative}')
            replacement = definition_bytes if relative.as_posix() == 'fleet.json' else None
            size = len(replacement) if replacement is not None else path.stat().st_size
            if out.tell() + size + 10240 > MAX_ARTIFACT:
                raise ValueError('App code package exceeds 32 MiB; use pinned images for large dependencies')
            entry = tarfile.TarInfo(relative.as_posix())
            entry.size = size
            entry.mode = 0o500 if path.stat().st_mode & 0o111 else 0o400
            if replacement is not None:
                archive.addfile(entry, io.BytesIO(replacement))
            else:
                with path.open('rb') as stream:
                    archive.addfile(entry, stream)
    payload = out.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


class FleetLifecycle:
    def __init__(self, resolver):
        self.resolver = resolver
        self._platforms = {}
        self.staged_snapshot = None

    async def _client(self, node_id):
        from pantheon.apps.builtin.fleet.inventory import node_inventory
        if not re.fullmatch(r'[A-Za-z0-9_-]+', node_id or ''):
            raise ValueError('A concrete Fleet node is required')
        await self.resolver._ensure_client()
        # Placement has just refreshed this inventory. Reuse that brief view;
        # the actual node RPC still verifies current instance state/membership.
        inventory = node_inventory(await self.resolver._list_nodes(max_age=2))
        node = next((node for node in inventory['nodes'] if node['node_id'] == node_id), None)
        if not node:
            raise ValueError('Node is not in this user’s Fleet')
        if node['status'] not in ('online', 'busy'):
            raise RuntimeError('Node is offline; lifecycle outcome is unknown until it reconnects')
        if node.get('runtimes', {}).get('app-lifecycle') != '1':
            raise RuntimeError('Upgrade Fleet on this node to enable managed App lifecycle v1')
        self._platforms[node_id] = f"{node.get('os')}-{node.get('arch')}"
        return self.resolver._client

    async def _request(self, node_id: str, method: str, **data):
        client = await self._client(node_id)
        result = await client.lifecycle(node_id, method, **data)
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result

    async def status(self, node_id: str):
        return await self._request(node_id, 'status')

    async def fence_start(self, node_id: str, request: dict):
        """Prevent a missing exact group start; never cancel accepted work.

        This is an idempotent negative acknowledgement. The caller must retain
        the original durable intent and inspect the returned/existing operation.
        Old nodes reject this method explicitly rather than emulating a fence.
        """
        required = {'protocol', 'operation_id', 'action', 'digest', 'scope', 'generation'}
        allowed = required | {'start_preparation_id'}
        if (not isinstance(request, dict) or not required <= request.keys() or request.keys() - allowed
                or request['protocol'] != PROTOCOL or request['action'] not in {'prepare_start', 'start'}
                or (request['action'] == 'start' and not request.get('start_preparation_id'))):
            raise ValueError('Fence the complete original preparation or prepared start request')
        result = await self._request(node_id, 'fence_start', request=request)
        return result['operation']

    async def model_idle(self, node_id: str, action: str, *, registration: dict):
        if action not in {'register', 'cancel'}:
            raise ValueError('Unsupported model idle management action')
        return await self._request(node_id, 'model_idle_' + action, model_idle=registration)

    async def group_peer(self, binding: dict, *, certificate: str | None = None,
                         authority: str | None = None):
        """Enroll/install for an exact prepared generation, never transfer keys.

        The node reads the roster and CA pin from its installed artifact. The
        returned generation is the future start (prepared generation + 1).
        A lost reply is retried with the same binding and certificate bytes.
        """
        required = {'node_id', 'instance_id', 'revision', 'generation'}
        if (not isinstance(binding, dict) or set(binding) != required
                or not isinstance(binding['node_id'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', binding['node_id'])
                or not isinstance(binding['instance_id'], str)
                or not re.fullmatch(r'[a-f0-9]{32}', binding['instance_id'])
                or not isinstance(binding['revision'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', binding['revision'])
                or type(binding['generation']) is not int
                or not 0 < binding['generation'] < 2**63 - 1):
            raise ValueError('Group credentials require an exact prepared instance binding')
        data = {key: binding[key] for key in required - {'node_id'}}
        method = 'group_peer_enroll'
        if certificate is not None or authority is not None:
            if any(not isinstance(value, str) or not 0 < len(value) <= 16384
                   for value in (certificate, authority)):
                raise ValueError('Install requires bounded certificate and CA PEM text')
            method = 'group_peer_install'
            data.update(certificate_pem=certificate, ca_pem=authority)
        result = await self._request(binding['node_id'], method, **data)
        if (result.get('protocol') != 1 or result.get('instance_id') != binding['instance_id']
                or result.get('revision') != binding['revision']
                or result.get('generation') != binding['generation'] + 1):
            raise RuntimeError('Node returned credentials for a different group attempt')
        return result

    async def group_authority(self, node_id: str, action: str, *, topology=None,
                              group_id='', topology_sha256='', claim=None):
        """Public certificate operations on the pinned leader; no key transfer.

        Prepare must precede artifact construction (the CA hash is in each
        package). Close is terminal and fences even a delayed initial Prepare.
        Other methods require the original group ID and topology fingerprint.
        """
        from pantheon.models.group_network import PeerTopology
        if action not in {'prepare', 'status', 'issue', 'close'}:
            raise ValueError('Unsupported group authority action')
        if action == 'prepare':
            if group_id or topology_sha256 or claim is not None:
                raise ValueError('Prepare takes only the complete pinned topology')
            peers = PeerTopology(topology)
            if peers.member(0)['node_id'] != node_id:
                raise ValueError('The original rank-zero node owns the group authority')
            group_id, topology_sha256 = peers.document()['group_id'], peers.fingerprint
            args = {'group_topology': peers.document()}
        else:
            if (topology is not None or not isinstance(group_id, str)
                    or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', group_id)
                    or not isinstance(topology_sha256, str)
                    or not re.fullmatch(r'[a-f0-9]{64}', topology_sha256)):
                raise ValueError('Use the original group ID and topology fingerprint')
            args = dict(group_id=group_id, topology_sha256=topology_sha256)
            if action == 'issue':
                required = {'rank', 'node_id', 'instance_id', 'revision', 'scope',
                            'generation', 'preparation_id', 'csr_pem'}
                if (not isinstance(claim, dict) or set(claim) != required
                        or type(claim['rank']) is not int or not 0 <= claim['rank'] < 16
                        or type(claim['generation']) is not int
                        or not 1 <= claim['generation'] < 2**63
                        or any(not isinstance(claim[key], str) or not re.fullmatch(pattern, claim[key])
                               for key, pattern in (('node_id', r'[A-Za-z0-9_-]{1,100}'),
                                   ('instance_id', r'[a-f0-9]{32}'), ('revision', r'[a-f0-9]{64}'),
                                   ('scope', r'[a-z0-9][a-z0-9_-]{0,79}'),
                                   ('preparation_id', r'[a-z0-9][a-z0-9_-]{0,79}')))
                        or not isinstance(claim['csr_pem'], str)
                        or not 0 < len(claim['csr_pem']) <= 16384):
                    raise ValueError('Issue requires the exact enrolled rank and public CSR')
                args['group_claim'] = claim
            elif claim is not None:
                raise ValueError('This authority action takes no enrollment claim')
        result = await self._request(node_id, 'group_authority_' + action, **args)
        if (type(result.get('protocol')) is not int or result['protocol'] != 1
                or result.get('group_id') != group_id
                or result.get('topology_sha256') != topology_sha256):
            raise RuntimeError('Node returned a different group authority identity')
        if action == 'issue':
            if any(type(result.get(key)) is not type(claim[key]) or result[key] != claim[key]
                   for key in ('rank', 'node_id', 'instance_id', 'revision', 'generation')):
                raise RuntimeError('Node returned a certificate for another group member')
            if any(not isinstance(result.get(key), str) or not 0 < len(result[key]) <= 16384
                   for key in ('certificate_pem', 'ca_pem')):
                raise RuntimeError('Node returned incomplete public certificates')
        elif result.get('node_id') != node_id or result.get('state') not in {'open', 'closed'}:
            raise RuntimeError('Node returned an invalid group authority state')
        if action == 'prepare' and result.get('owner') != peers.document()['owner']:
            raise RuntimeError('Node returned another Fleet owner’s group authority')
        return result

    async def group_overlay(self, node_id: str, action: str, *, topology,
                            rank=None, ca_sha256=None, address=None, endpoints=None):
        """Enroll only public network material on the exact authenticated node."""
        from pantheon.models.group_network import PeerTopology
        from pantheon.models.group_overlay import check_reply, private_endpoint, validate_network
        peers = PeerTopology(topology)
        if peers.document() != topology or node_id not in {p['node_id'] for p in topology['members']}:
            raise ValueError('Use the exact canonical topology and member node')
        if action == 'prepare':
            if (type(rank) is not int or peers.member(rank)['node_id'] != node_id
                    or not isinstance(ca_sha256, str) or not re.fullmatch('[a-f0-9]{64}', ca_sha256)
                    or endpoints is not None):
                raise ValueError('Prepare only the original local rank manifest')
            args = dict(manifest=dict(protocol=1, rank=rank, topology=topology, ca_sha256=ca_sha256),
                        address=private_endpoint(address))
        elif action in {'pin', 'status', 'close'}:
            if rank is not None or ca_sha256 is not None or address is not None:
                raise ValueError('Existing overlay operations cannot replace enrollment')
            args = dict(group_id=topology['group_id'], topology_sha256=peers.fingerprint)
            if action == 'pin':
                if not isinstance(endpoints, list) or not endpoints:
                    raise ValueError('Pin the complete original public endpoint roster')
                validate_network(dict(addresses=[e.get('address') if isinstance(e, dict) else None for e in endpoints],
                    endpoints=endpoints, ready=True, closed=False), len(topology['members']))
                args['endpoints'] = endpoints
            elif endpoints is not None:
                raise ValueError('Only pin accepts a public endpoint roster')
        else:
            raise ValueError('Unsupported group overlay action')
        result = await self._request(node_id, 'group_overlay_' + action, group_overlay=args)
        check_reply(result, node_id, peers)
        if action == 'prepare' and result['state'] != 'closed' and (
                result['endpoint']['rank'] != rank or result['endpoint']['address'] != address):
            raise RuntimeError('Node changed the original local network enrollment')
        if action == 'pin' and (result['state'] != 'pinned' or result['endpoints'] != endpoints):
            raise RuntimeError('Node did not acknowledge the exact pinned roster')
        if action == 'close' and result['state'] != 'closed':
            raise RuntimeError('Node did not close the original network enrollment')
        return result

    async def resource_status(self, node_id: str):
        """Measured capacity and node policy; unknown telemetry is not free RAM."""
        result = await self._request(node_id, 'resource_status')
        if result.get('protocol') != 1:
            raise RuntimeError('Upgrade Fleet on this node for resource reservations')
        return result

    async def reserve_resources(self, binding: dict, lease_id: str, resources: dict):
        """Reserve before loading; keep this immutable lease until confirmed unload.

        A lost reply must be retried with the SAME lease id and budget. Do not
        expire a lease merely because a UI/Agent connection has disappeared.
        """
        return await self._request(binding['node_id'], 'resource_reserve',
            instance_id=binding['instance_id'], revision=binding['revision'],
            generation=binding['generation'], lease_id=lease_id, resources=resources)

    async def release_resources(self, binding: dict, lease_id: str):
        return await self._request(binding['node_id'], 'resource_release',
            instance_id=binding['instance_id'], revision=binding['revision'],
            generation=binding['generation'], lease_id=lease_id)

    async def stage(self, node_id: str, directory: Path, *, immutable_revision: str | None = None):
        client = await self._client(node_id)
        platform = self._platforms.get(node_id)
        workspace = getattr(self.resolver, '_workdir', None) if node_id == getattr(self.resolver, '_node', None) else None
        # Only Desktop's immutable snapshots opt in. Mutable checkouts must
        # always be repackaged. Scope the cache to this resolver/process so an
        # adapter rollout cannot accidentally reuse an older generated package.
        cache = getattr(self.resolver, '_staged_app_digests', None)
        key = (str(directory), immutable_revision, platform, workspace)
        cacheable = self.resolver is not None and bool(re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', immutable_revision or ''))
        if cacheable and cache is None:
            cache = self.resolver._staged_app_digests = OrderedDict()
        snapshot = await client.lifecycle(node_id, 'status')
        if snapshot.get('error'):
            raise RuntimeError(snapshot['error'])
        self.staged_snapshot = snapshot
        if cacheable and key in cache:
            digest = cache[key]
            if snapshot.get('installations', {}).get(digest, {}).get('state') == 'installed':
                cache.move_to_end(key)
                return digest
        def package():
            from pantheon.apps.portable import execution_package
            with execution_package(directory, platform, workspace=workspace) as root:
                return build_artifact(root, platform)
        payload, digest = await asyncio.to_thread(package)
        if cacheable:
            cache[key] = digest
            cache.move_to_end(key)
            while len(cache) > 128:
                cache.popitem(last=False)
        if snapshot.get('installations', {}).get(digest, {}).get('state') == 'installed':
            return digest
        # Reuse the authenticated connection across chunks; this is code only,
        # never a bulk document transfer. All chunks are offset/idempotent.
        for offset in range(0, len(payload), CHUNK_SIZE):
            result = await client.lifecycle(node_id, 'stage', digest=digest, offset=offset,
                data=base64.b64encode(payload[offset:offset + CHUNK_SIZE]).decode())
            if result.get('error'):
                raise RuntimeError(result['error'])
        return digest

    async def stage_exact(self, node_id: str, payload: bytes, digest: str):
        """Stage an already compiled immutable artifact without repackaging it.

        Validate before contacting Fleet. Re-delivery uses original byte offsets;
        only a fresh installed-digest snapshot can skip the transfer. Installation
        and engine start remain separately journaled lifecycle operations.
        """
        if (not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_ARTIFACT
                or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
                or hashlib.sha256(payload).hexdigest() != digest):
            raise ValueError('Stage only the bounded original artifact bytes and digest')
        client = await self._client(node_id)
        snapshot = await client.lifecycle(node_id, 'status')
        if snapshot.get('error'):
            raise RuntimeError(snapshot['error'])
        self.staged_snapshot = snapshot
        if snapshot.get('installations', {}).get(digest, {}).get('state') == 'installed':
            return digest
        for offset in range(0, len(payload), CHUNK_SIZE):
            result = await client.lifecycle(node_id, 'stage', digest=digest, offset=offset,
                data=base64.b64encode(payload[offset:offset + CHUNK_SIZE]).decode())
            if result.get('error'):
                raise RuntimeError(result['error'])
        return digest

    async def submit(self, node_id: str, action: str, digest: str, *, scope='app',
                     generation=0, operation_id: str | None = None, data_source: dict | None = None,
                     start_preparation_id: str | None = None):
        if action not in {'install', 'uninstall', 'start', 'prepare_start', 'stop', 'reconcile', 'recover', 'clone_data'}:
            raise ValueError('Unsupported lifecycle operation')
        if (action == 'clone_data') != (data_source is not None):
            raise ValueError('State copy requires an exact source binding')
        if start_preparation_id is not None and (action != 'start' or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', start_preparation_id)):
            raise ValueError('Prepared start requires an exact preparation operation id')
        if action == 'prepare_start' and not operation_id:
            raise ValueError('Choose a stable operation_id before preparing a start')
        result = await self._request(node_id, 'submit', request={
            'protocol': PROTOCOL, 'operation_id': operation_id or uuid.uuid4().hex,
            'action': action, 'digest': digest, 'scope': scope, 'generation': generation,
            **({'data_source': data_source} if data_source is not None else {}),
            **({'start_preparation_id': start_preparation_id} if start_preparation_id is not None else {}),
        })
        return result['operation']

    async def usage(self, node_id: str, method: str, *, instance_id: str,
                    revision: str, generation: int, lease_id: str = '',
                    release: bool = False, keep_alive: bool = False):
        if method not in {'lease', 'keep_alive'}:
            raise ValueError('Unsupported App usage operation')
        return await self._request(node_id, method, instance_id=instance_id,
            revision=revision, generation=generation, lease_id=lease_id,
            release=release, keep_alive=keep_alive)
