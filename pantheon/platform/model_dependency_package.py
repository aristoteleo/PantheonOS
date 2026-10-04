"""Build the stateless, versioned Model Services dependency control App.

Only httpx is required. Engine connectors, model frameworks, Agent and NATS are
not bundled. Policies and the Hub credential arrive via Fleet prepared config.
"""
import argparse
import json
from pathlib import Path
import shutil

from pantheon.apps.portable import definition


def build_package(destination, platform):
    if platform not in {f'{os}-{arch}' for os in ('linux', 'darwin', 'windows') for arch in ('amd64', 'arm64')}:
        raise ValueError('Unsupported Model Services control platform')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).parents[1]
    manifest = {
        'apiVersion': 2, 'id': 'model-services-control', 'name': 'Model Services access',
        'version': '0.1.0', 'kind': 'service', 'surface': 'headless', 'runtime': 'process',
        'entry': {'backend': 'backend/__init__.py'},
        'execution': {'protocol': 1, 'manifest': 'fleet.json'},
        'provides': {
            'interfaces': [{'name': 'model-inference', 'version': 1, 'tools': ['model_services_control']}],
            'tools': [{'name': 'model_services_control', 'params': [
                {'name': name, 'type': kind, 'required': True} for name, kind in (
                    ('policy_id', 'str'), ('operation', 'str'), ('arguments', 'dict'))]}]},
        'notes': 'Trusted Model Services facade. Consumer grants must bind policy_id. Hub credentials stay here.',
    }
    (destination / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
    backend = destination / 'backend'
    backend.mkdir()
    (backend / '__init__.py').write_text('from pantheon.platform.model_dependency_host import register\n')
    vendor = backend / '_vendor' / 'pantheon'
    modules = (
        'platform/model_dependency_host.py', 'platform/model_dependency_control.py', 'platform/registry_lock.py',
        'models/dependency_service.py', 'models/errors.py',
        'apps/runtime_config.py', 'apps/dependency_assembly.py', 'apps/owner_journal.py',
    )
    for name in modules:
        target = vendor / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    for directory in (vendor, vendor / 'platform', vendor / 'models', vendor / 'apps'):
        (directory / '__init__.py').write_text('')
    (destination / 'requirements.txt').write_text('httpx==0.28.1\n')
    adapter = destination / '.fleet-runtime'
    adapter.mkdir()
    from pantheon.apps.builtin.desktop import app_runtime
    shutil.copyfile(app_runtime.__file__, adapter / 'app_runtime.py')
    for name in ('host.py', 'install.py', 'launch.py'):
        shutil.copyfile(source / 'apps' / 'portable_runtime' / name, adapter / name)
    execution = definition(manifest, platform)
    execution['components'][0]['configuration'] = {
        'values': {'model_services': {'required': True}},
        'credentials': {'hub': {'required': True}},
    }
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
