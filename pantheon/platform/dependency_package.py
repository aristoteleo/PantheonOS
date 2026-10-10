"""Build the trusted allocation service using the ordinary native App host.

The output is immutable code only: no owner policies, credentials or journals.
Prepare/configure it with endpoint-bound node vault references before starting.
"""
import argparse
import json
from pathlib import Path
import shutil

from pantheon.apps.portable import definition


def build_package(destination, platform):
    if platform not in {'linux-amd64', 'linux-arm64', 'darwin-amd64', 'darwin-arm64'}:
        raise ValueError('Dependency owner storage currently requires a POSIX node')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).parents[1]
    manifest = {
        'apiVersion': 2, 'id': 'dependency-binding', 'name': 'Dependency allocator',
        'version': '0.1.1', 'kind': 'service', 'surface': 'headless', 'runtime': 'process',
        'entry': {'backend': 'backend/__init__.py'},
        'execution': {'protocol': 1, 'manifest': 'fleet.json'},
        'provides': {
            'interfaces': [{'name': 'dependency-binding', 'version': 1, 'tools': ['bind_dependencies', 'retire_dependencies']}],
            'tools': [{'name': 'bind_dependencies', 'params': [
                {'name': name, 'type': kind, 'required': True} for name, kind in (
                    ('policy_id', 'str'), ('owner_ref', 'str'),
                    ('operation_id', 'str'), ('aliases', 'list[str]'))]},
                {'name': 'retire_dependencies', 'params': [
                    {'name': name, 'type': 'str', 'required': True} for name in ('policy_id', 'owner_ref')]}]},
        'notes': 'Trusted platform service. Bind policy_id in consumer grants; never expose owner credentials to consumers.',
    }
    (destination / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
    backend = destination / 'backend'
    backend.mkdir()
    (backend / '__init__.py').write_text('from pantheon.platform.dependency_host import register\n')
    vendor = backend / '_vendor' / 'pantheon'
    modules = (
        'platform/dependency_host.py', 'platform/dependency_control.py', 'utils/registry_lock.py',
        'apps/runtime_config.py', 'apps/dependency_binding_service.py', 'apps/live_dependencies.py',
        'apps/dependency_assembly.py', 'apps/resource_sessions.py', 'apps/owner_journal.py',
        'apps/lifecycle.py', 'apps/client.py',
    )
    for name in modules:
        target = vendor / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    for directory in (vendor, vendor / 'platform', vendor / 'apps'):
        (directory / '__init__.py').write_text('')
    # Only the platform control dependencies, no Agent/model/UI framework.
    (destination / 'requirements.txt').write_text('httpx==0.28.1\nnats-py[nkeys]==2.12.0\n')
    adapter = destination / '.fleet-runtime'
    adapter.mkdir()
    from pantheon.apps.builtin.desktop import app_runtime
    shutil.copyfile(app_runtime.__file__, adapter / 'app_runtime.py')
    for name in ('host.py', 'install.py', 'launch.py'):
        shutil.copyfile(source / 'apps' / 'portable_runtime' / name, adapter / name)
    execution = definition(manifest, platform)
    execution['components'][0]['configuration'] = {
        'values': {'dependency_binding': {'required': True}},
        'credentials': {'hub': {'required': True}, 'controller': {'required': True}},
    }
    # Component hooks receive the Runner's current port and RPC token. An
    # unbound package hook deliberately receives neither credential.
    execution['hooks']['before_stop']['component'] = 'backend'
    (destination / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    build_package(args.output, args.platform)


if __name__ == '__main__':
    main()
