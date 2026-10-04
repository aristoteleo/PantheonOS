"""Provision the existing platform budget for a node's Model Service Connector.

Run with the owner's explicitly paired Hub login and a selected local/remote vault.
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

from pantheon.apps.dependency_assembly import AssemblyError

from .credentials import LocalModelCredentialVault, RemoteModelCredentialVault, model_credential_endpoint, _regular_file


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate platform budget response field')
        result[key] = value
    return result


def budget_connector(configuration):
    """A non-secret, exact destination approved in an owner startup recipe."""
    if (not isinstance(configuration, dict) or set(configuration) != {'engine', 'endpoint', 'secret_ref'}
            or configuration['engine'] != 'api'
            or not isinstance(configuration['secret_ref'], str)
            or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', configuration['secret_ref'])):
        raise ValueError('Platform budget requires an API Connector and an exact node credential reference')
    endpoint = model_credential_endpoint(configuration['endpoint'])
    if endpoint != configuration['endpoint'] or not urlsplit(endpoint).path.endswith('/v1'):
        raise ValueError('Use the complete, canonical platform budget API prefix')
    return dict(configuration)


def budget_receipt(value, *, owner, node_id, connector):
    """Validate before saving a provisioning acknowledgement to an owner journal."""
    if (not isinstance(value, dict) or set(value) != {'protocol', 'owner', 'node_id', 'source', 'model_mode', 'connector'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or value['owner'] != owner or value['node_id'] != node_id or value['source'] != 'platform-budget'
            or value['model_mode'] not in ('direct', 'openrouter') or value['connector'] != budget_connector(connector)):
        raise ValueError('Budget acknowledgement does not match the original startup intent')
    return {**value, 'connector': dict(value['connector'])}


async def provision_platform_budget(*, hub, token, vault, ref, transport=None, expected_connector=None):
    """Idempotent provisioning, with no rotation or fallback on conflict.

    A full owner login (not a workload/Agent token) is required by the existing
    /me/llm-proxy endpoint. A changed owner, node, upstream or existing key fails
    before replacement. The owner must explicitly recover an expired/rotated key.
    """
    if not isinstance(vault, (LocalModelCredentialVault, RemoteModelCredentialVault)):
        raise ValueError('Supply the selected Fleet credential vault')
    if expected_connector is not None:
        expected_connector = budget_connector(expected_connector)
        if expected_connector['secret_ref'] != ref:
            raise ValueError('Budget credential reference differs from the startup recipe')
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
    await vault.check_async()
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
    connector = {'engine': 'api', 'endpoint': endpoint, 'secret_ref': ref}
    if expected_connector is not None and connector != expected_connector:
        raise ValueError('Platform budget endpoint changed; review the startup recipe before credential delivery')
    # Vault adapters join accepted mutations even if this caller is cancelled.
    await vault.ensure_async(ref, endpoint, key)
    return {'protocol': 1, 'owner': vault.owner, 'node_id': vault.node_id,
            'source': 'platform-budget', 'model_mode': result['model_mode'],
            'connector': connector}


class BudgetCredentialPreparer:
    """Explicit owner login paired with a startup host, never part of its recipe."""
    def __init__(self, *, hub, token_file):
        self.hub, self.token_file = hub, Path(token_file)
        if os.name != 'posix' or not self.token_file.is_absolute():
            raise ValueError('Supply an absolute owner-private Hub login file on the platform host')

    async def __call__(self, *, owner, node_id, connector, lifecycle):
        token = await asyncio.to_thread(_private_token, self.token_file)
        vault = RemoteModelCredentialVault(lifecycle, owner=owner, node_id=node_id)
        return await provision_platform_budget(hub=self.hub, token=token, vault=vault,
            ref=connector['secret_ref'], expected_connector=connector)


def _private_token(path):
    if not path.is_absolute():
        raise ValueError('Use an absolute private credential file')
    with os.fdopen(_regular_file(path), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16385:
            raise ValueError('Token must be in a bounded owner-private file')
        token = stream.read(16386).decode().strip()
    if not token or len(token) > 16384 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError('Invalid private token file')
    return token


async def _provision_command(args, token):
    if args.controller:
        from pantheon.apps.runtime_config import RuntimeCredential
        from pantheon.platform.dependency_control import OwnerCredentialLifecycle
        lifecycle = OwnerCredentialLifecycle(owner=args.owner, credential=RuntimeCredential(
            args.controller, _private_token(args.controller_token_file)))
        try:
            return await provision_platform_budget(hub=args.hub, token=token,
                vault=RemoteModelCredentialVault(lifecycle, owner=args.owner, node_id=args.node_id), ref=args.ref)
        finally:
            await lifecycle.close()
    vault = LocalModelCredentialVault(args.fleet_executable, state_dir=args.state_dir,
                                     owner=args.owner, node_id=args.node_id)
    return await provision_platform_budget(hub=args.hub, token=token, vault=vault, ref=args.ref)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub', required=True)
    parser.add_argument('--token-file', required=True, type=Path)
    parser.add_argument('--fleet-executable')
    parser.add_argument('--state-dir')
    parser.add_argument('--controller', help='Explicit HTTPS Fleet controller for a remote node')
    parser.add_argument('--controller-token-file', type=Path, help='Private owner Fleet credential file; never sent to the node')
    parser.add_argument('--owner', required=True)
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        local, remote = bool(args.fleet_executable or args.state_dir), bool(args.controller or args.controller_token_file)
        if (local == remote or local and not (args.fleet_executable and args.state_dir)
                or remote and not (args.controller and args.controller_token_file)):
            raise ValueError('Select either a complete local vault or a remote owner controller connection')
        if os.name != 'posix' or not args.token_file.is_absolute() or not args.output.is_absolute():
            raise ValueError('This owner command requires POSIX and absolute private file paths')
        if args.output.exists() or args.output.is_symlink():
            raise ValueError('Choose a new descriptor path; existing output is never overwritten')
        token = _private_token(args.token_file)
        result = asyncio.run(_provision_command(args, token))
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError, AssemblyError):
        parser.exit(1, 'Platform budget provisioning failed; inspect the paired Hub, node identity and credential reference. No existing credential or descriptor was replaced.\n')


if __name__ == '__main__':
    main()
