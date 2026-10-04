"""Provision the existing platform budget for a node's Model Service Connector.

Run on the chosen Fleet node, with the owner's explicitly paired Hub login.
The Hub reuses its per-user LiteLLM key. Only Fleet's existing private vault
receives it. The returned descriptor contains a normal API connector config;
it neither starts a service nor publishes models or changes Agent preferences.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import ssl
import stat
from urllib.parse import urlsplit

import httpx

from pantheon.apps.dependency_binding_client import _drain
from .credentials import LocalModelCredentialVault, model_credential_endpoint, _regular_file


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate platform budget response field')
        result[key] = value
    return result


async def provision_platform_budget(*, hub, token, vault, ref, transport=None):
    """Idempotent local provisioning, with no rotation or fallback on conflict.

    A full owner login (not a workload/Agent token) is required by the existing
    /me/llm-proxy endpoint. A changed owner, node, upstream or existing key fails
    before replacement. The owner must explicitly recover an expired/rotated key.
    """
    if not isinstance(vault, LocalModelCredentialVault):
        raise ValueError('Supply the selected local Fleet credential vault')
    if (not isinstance(ref, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref)
            or not re.fullmatch(r'f_[a-f0-9]{16}', vault.owner)
            or not isinstance(token, str) or not 1 <= len(token) <= 16384
            or any(not 33 <= ord(c) <= 126 for c in token)):
        raise ValueError('Supply an explicit platform owner login and credential reference')
    try:
        origin = urlsplit(hub)
        if (not isinstance(hub, str) or len(hub) > 2048 or origin.scheme != 'https'
                or not origin.hostname or origin.path not in ('', '/')
                or origin.username or origin.password or '?' in hub or '#' in hub
                or any(c.isspace() for c in hub)):
            raise ValueError
        origin.port
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Supply the explicitly paired HTTPS Hub origin') from None
    vault._check_node()
    # Do not follow redirects or inherit ambient proxy/auth environment. A
    # response or transport error can contain the user's virtual/login key.
    try:
        async with asyncio.timeout(20), httpx.AsyncClient(
                timeout=15, trust_env=False, follow_redirects=False,
                verify=ssl.create_default_context(), transport=transport) as client:
            async with client.stream('GET', hub.rstrip('/') + '/api/users/me/llm-proxy',
                    headers={'Authorization': 'Bearer ' + token}) as response:
                if response.status_code != 200:
                    raise ValueError
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 32768:
                        raise ValueError
        result = json.loads(raw, object_pairs_hook=_unique)
        if (not isinstance(result, dict) or result.get('fleet_id') != vault.owner
                or result.get('model_mode') not in ('direct', 'openrouter')):
            raise ValueError
        endpoint = model_credential_endpoint(result.get('api_base_url'))
        # Hub supplies the complete API base. Do not infer routing mode from
        # model names, remap IDs, or silently insert a prefix in the connector.
        if not urlsplit(endpoint).path.endswith('/v1'):
            raise ValueError
        key = result.get('virtual_key')
        if not isinstance(key, str) or not 1 <= len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError
    except (httpx.HTTPError, ValueError, TypeError, TimeoutError, UnicodeError):
        raise ValueError('Platform budget unavailable or not bound to this Fleet owner; update or sign in to the paired Hub') from None
    # Shield the local mutation: a cancelled request must still join its child
    # instead of reporting completion while provisioning continues unobserved.
    task = asyncio.create_task(asyncio.to_thread(vault.ensure, ref, endpoint, key))
    await _drain(task)
    return {'protocol': 1, 'owner': vault.owner, 'node_id': vault.node_id,
            'source': 'platform-budget', 'model_mode': result['model_mode'],
            'connector': {'engine': 'api', 'endpoint': endpoint, 'secret_ref': ref}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub', required=True)
    parser.add_argument('--token-file', required=True, type=Path)
    parser.add_argument('--fleet-executable', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if os.name != 'posix' or not args.token_file.is_absolute() or not args.output.is_absolute():
            raise ValueError('This owner command requires POSIX and absolute private file paths')
        if args.output.exists() or args.output.is_symlink():
            raise ValueError('Choose a new descriptor path; existing output is never overwritten')
        with os.fdopen(_regular_file(args.token_file), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16385:
                raise ValueError('Hub token must be in a bounded owner-private file')
            token = stream.read(16386).decode().strip()
        vault = LocalModelCredentialVault(args.fleet_executable, state_dir=args.state_dir,
                                         owner=args.owner, node_id=args.node_id)
        result = asyncio.run(provision_platform_budget(hub=args.hub, token=token, vault=vault, ref=args.ref))
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError):
        parser.exit(1, 'Platform budget provisioning failed; inspect the paired Hub, node identity and credential reference. No existing credential or descriptor was replaced.\n')


if __name__ == '__main__':
    main()
