"""Explicit private credentials for an owned local App composition.

Only references already selected in the composition can be provisioned. The
source is read once, never included in recipes, and delivered by the existing
Fleet vault. This is not credential discovery, replacement or rotation.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re

from pantheon.apps.credentials import _regular_file, app_credential_endpoint, credential_identity
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.runtime_config import RuntimeCredential
from .app_preset import _unique_fields


@dataclass(frozen=True)
class LocalCredentials:
    entries: tuple = field(repr=False)

    async def deliver(self, vault):
        for ref, credential in self.entries:
            await vault.ensure_async(ref, credential.endpoint, credential.key)


def _identity(endpoint):
    return credential_identity(app_credential_endpoint(endpoint))


def _declared(spec):
    references = {}
    apps = list(spec['apps'].values()) + [item['app'] for item in spec['model_apps'].values()]
    def add(ref, endpoint):
        identity = _identity(endpoint)
        if ref in references and references[ref] != identity:
            raise ValueError('Conflicting credential endpoints')
        references[ref] = identity
    for app in apps:
        for component in app['components'].values():
            for value in component.get('credentials', {}).values():
                if '$local' not in value:
                    add(value['ref'], value['endpoint'])
    for item in spec['model_apps'].values():
        for component in item['app']['components'].values():
            connector = component.get('values', {}).get('connector', {})
            if 'secret_ref' in connector:
                add(connector['secret_ref'], connector['endpoint'])
    return references


def _public_roots(spec, workspace):
    """Keep source secrets out of workspace, uploaded code and Desktop assets."""
    yield Path(workspace).resolve()
    for package in spec.get('packages', {}).values():
        yield Path(package['path']).resolve()
    apps = list(spec['apps'].values()) + [item['app'] for item in spec['model_apps'].values()]
    for app in apps:
        for component in app['components'].values():
            desktop = component.get('values', {}).get('desktop')
            if not isinstance(desktop, dict):
                continue
            for root in desktop.get('data_roots', []):
                if isinstance(root, str): yield Path(root).resolve()
            for catalog in desktop.get('catalog', []):
                root = Path(catalog['path']).resolve()
                yield root
                if catalog['scope'] == 'user':
                    for name in ('snapshots', 'forks', 'repositories'):
                        yield (root.parent/'app-store'/name).resolve()


def read_credentials(path, spec, workspace):
    """Read a bounded private file before launching infrastructure or Apps.

    Check permissions on the opened descriptor, not just the pathname. Never
    expose decoder exceptions, source contents or provider values to callers.
    """
    try:
        path = Path(path)
        if not path.is_absolute() or any(path.resolve().is_relative_to(root) for root in _public_roots(spec, workspace)):
            raise ValueError('Credentials must be outside the served workspace')
        fd = _regular_file(path)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError('Credentials must be owner-private')
            raw = stream.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError('Credentials exceed their limit')
        value = json.loads(raw, object_pairs_hook=_unique_fields)
        if (not isinstance(value, dict) or set(value) != {'protocol', 'credentials'}
                or type(value['protocol']) is not int or value['protocol'] != 1
                or not isinstance(value['credentials'], dict) or len(value['credentials']) > 64):
            raise ValueError('Invalid credential source')
        declared = _declared(spec)
        entries = []
        for ref, credential in value['credentials'].items():
            if (not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref)
                    or ref.startswith(('node-secret://profile-owner-', 'node-secret://profile-bus-'))
                    or not isinstance(credential, dict) or set(credential) != {'endpoint', 'key'}):
                raise ValueError('Invalid credential entry')
            endpoint, key = credential['endpoint'], credential['key']
            if (ref not in declared or _identity(endpoint) != declared[ref]
                    or not isinstance(key, str) or not 0 < len(key) <= 8192
                    or any(not 33 <= ord(c) <= 126 for c in key)):
                raise ValueError('Credential does not match the selected composition')
            entries.append((ref, RuntimeCredential(endpoint, key)))
        return LocalCredentials(tuple(entries))
    except Exception:
        raise AssemblyError('Invalid private credential source: use an owner-only regular file outside workspace, package and served asset roots '
                            'with only selected references and their matching endpoints') from None
