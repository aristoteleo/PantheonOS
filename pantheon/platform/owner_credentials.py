"""Provision a revocable platform key for trusted owner-control Apps.

The key is explicitly supplied by the owner, not discovered from login state.
Only selected node vaults receive it. Output contains endpoint-bound references
for ordinary prepared App configuration; this command never starts an App.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.credentials import RemoteModelCredentialVault
from pantheon.models.platform_budget import _private_token
from pantheon.platform.dependency_control import OwnerCredentialLifecycle


def _endpoint(value):
    try:
        parts = urlsplit(value)
        if (not isinstance(value, str) or len(value) > 2048 or parts.scheme != 'https'
                or not parts.hostname or parts.username or parts.password
                or '?' in value or '#' in value or any(c.isspace() for c in value)):
            raise ValueError
        parts.port
        return value.rstrip('/')
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Use an explicitly paired HTTPS service endpoint') from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate identity field')
        result[key] = value
    return result


async def provision_owner_credentials(*, hub, key, owner, node_ids, ref_prefix,
                                      transport=None, tls_context=None):
    """Use the same references/key to resume a partial delivery, never rotate.

    These credentials belong to trusted allocator/model-access hosts. They must
    not be put in an Agent's configuration or dependency grant. A new key needs
    new references and an explicit host cutover before revoking the old key.
    """
    hub = _endpoint(hub)
    if (not isinstance(key, str) or not re.fullmatch(r'pbk_[A-Za-z0-9_-]{43}', key)
            or not isinstance(owner, str) or not re.fullmatch(r'f_[a-f0-9]{16}', owner)
            or not isinstance(node_ids, list) or not 1 <= len(node_ids) <= 16
            or any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', n) for n in node_ids)
            or len(set(node_ids)) != len(node_ids)
            or not isinstance(ref_prefix, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,52}', ref_prefix)):
        raise ValueError('Supply a platform key, Fleet owner, exact nodes and stable credential prefix')
    try:
        async with asyncio.timeout(20), httpx.AsyncClient(timeout=15, trust_env=False,
                follow_redirects=False, verify=tls_context or True, transport=transport) as client:
            async with client.stream('GET', hub + '/api/fleet/apps/workload-identity',
                    headers={'Authorization': 'Bearer ' + key}) as response:
                if response.status_code != 200:
                    raise ValueError
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 8192:
                        raise ValueError
        identity = json.loads(raw, object_pairs_hook=_unique)
        if (not isinstance(identity, dict) or set(identity) != {'protocol', 'fleet_id', 'controller_url'}
                or type(identity['protocol']) is not int or identity['protocol'] != 1
                or identity['fleet_id'] != owner):
            raise ValueError
        controller = _endpoint(identity['controller_url'])
    except (httpx.HTTPError, ValueError, TypeError, TimeoutError, UnicodeError):
        raise ValueError('The paired Hub did not confirm this workload owner and controller') from None

    lifecycle = OwnerCredentialLifecycle(owner=owner, credential=RuntimeCredential(controller, key),
                                         tls_context=tls_context)
    try:
        vaults = {node: RemoteModelCredentialVault(lifecycle, owner=owner, node_id=node) for node in node_ids}
        # Validate every selected destination before delivering any secret.
        for vault in vaults.values():
            await vault.check_async()
        refs = {alias: {'ref': f'node-secret://{ref_prefix}-{alias}', 'endpoint': endpoint}
                for alias, endpoint in [('hub', hub), ('controller', controller)]}
        for vault in vaults.values():
            for descriptor in refs.values():
                await vault.ensure_async(descriptor['ref'], descriptor['endpoint'], key)
        return {'protocol': 1, 'owner': owner, 'source': 'platform-key',
                'nodes': {node: {alias: dict(value) for alias, value in refs.items()} for node in node_ids}}
    finally:
        await lifecycle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub', required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--node-id', action='append', required=True, help='Repeat for each trusted control node')
    parser.add_argument('--ref-prefix', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if (os.name != 'posix' or not args.output.is_absolute() or not args.key_file.is_absolute()
                or args.output.exists() or args.output.is_symlink()):
            raise ValueError('Use private absolute paths and a new output file')
        key = _private_token(args.key_file)
        result = asyncio.run(provision_owner_credentials(hub=args.hub, key=key, owner=args.owner,
            node_ids=args.node_id, ref_prefix=args.ref_prefix))
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError, AssemblyError):
        parser.exit(1, 'Control credential provisioning failed. Inspect the paired Hub and selected nodes; '
            'delivery may be partial. Retry the same key and references; existing credentials are never replaced.\n')


if __name__ == '__main__':
    main()
