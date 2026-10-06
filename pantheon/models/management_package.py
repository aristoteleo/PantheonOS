"""Build the complete ordinary Model Services management App (owner-side)."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.portable import definition
from pantheon.apps.reflect import reflect_toolset_class
from pantheon.apps.schema import parse_manifest
from .management_tools import ModelManagementToolSet


SDK_FILES = '''toolset.py utils/log.py utils/misc.py utils/adapters/image_blocks.py
internal/package_runtime/context.py remote/backend/base.py
apps/runtime_config.py apps/owned_bus.py apps/fleet_controller.py apps/toolset_backend.py apps/resolver.py
apps/client.py apps/registry.py apps/reflect.py apps/schema.py apps/distribution.py
apps/lifecycle.py apps/portable.py apps/store_release.py apps/versioning.py
apps/compat.py apps/dependency_assembly.py apps/owner_journal.py
platform/registry_lock.py'''.split()
MODEL_MODULES = '''client direct direct_session engine_upgrade errors group_coordinator
 group_creation group_hub group_inference group_install group_journal group_management
 group_network group_overlay group_package group_security http_pool idle idle_management
 jobs local_directory managed management_app management_directory management_state management_tools manager media
 messages modal_gpu model_deploy model_metadata operation_stop prepared_registration recovery routing'''.split()


def build_package(destination, platform):
    if platform not in {'darwin-arm64', 'darwin-amd64', 'linux-arm64', 'linux-amd64'}:
        raise ValueError('Prepared model management currently requires a POSIX host')
    runtime = Path(__file__).resolve().parents[1]
    from pantheon.apps.registry import BUILTIN_ROOT
    connector = Path(BUILTIN_ROOT)/'model-service'
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.model-management-', dir=destination.parent) as temp:
        root = Path(temp)/'release'; root.mkdir()
        tools = [tool.model_dump(exclude_none=True) for tool in reflect_toolset_class(ModelManagementToolSet)]
        manifest = {'apiVersion': 2, 'id': 'model-services-management', 'name': 'Model Services management',
            'version': '0.1.0', 'runtime': 'process', 'surface': 'headless', 'kind': 'service',
            'description': 'Manage existing Model Services, deploy catalog or pinned models and control GPU nodes.',
            'entry': {'backend': 'backend/__init__.py'},
            'execution': {'protocol': 1, 'manifest': 'fleet.json'},
            'provides': {'tools': tools, 'interfaces': [{'name': 'model-management', 'version': 1,
                'tools': [tool['name'] for tool in tools if not tool.get('hidden')]}]}}
        parse_manifest(manifest)
        (root/'app.json').write_text(json.dumps(manifest, indent=2)+'\n')
        vendor = root/'backend/_vendor/pantheon'
        def copy(source, target):
            if source.is_symlink() or not source.is_file():
                raise ValueError('Model management inputs must be regular files')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for name in SDK_FILES:
            copy(runtime/name, vendor/name)
        for name in MODEL_MODULES:
            copy(runtime/'models'/(name+'.py'), vendor/'models'/(name+'.py'))
        for path in (runtime/'model_contracts').glob('*.py'):
            copy(path, vendor/'model_contracts'/path.name)
        for path in (runtime/'funcdesc').rglob('*.py'):
            copy(path, vendor/path.relative_to(runtime))
        copy(BUILTIN_ROOT/'fleet/inventory.py', vendor/'apps/builtin/fleet/inventory.py')
        for path in connector.rglob('*'):
            relative = path.relative_to(connector)
            if any(part in {'__pycache__', '.git'} for part in relative.parts) or path.suffix == '.pyc':
                continue
            if path.is_symlink():
                raise ValueError('Connector resource inputs must not contain links')
            if path.is_file():
                copy(path, vendor/'models/_connector'/relative)
        # No original package initializers: they import combined Agent/settings hosts.
        for directory in [vendor, *[p for p in vendor.rglob('*') if p.is_dir()]]:
            init = directory/'__init__.py'
            if not init.exists(): init.write_text('')
        (root/'backend/__init__.py').write_text('from pantheon.models.management_app import register\n')
        (root/'requirements.txt').write_text('loguru==0.7.3\nrich==14.3.2\npydantic==2.12.5\nnats-py[nkeys]==2.16.0\nhttpx==0.28.1\n')
        copy(BUILTIN_ROOT/'desktop/app_runtime.py', root/'.fleet-runtime/app_runtime.py')
        for name in ('host.py', 'install.py', 'launch.py'):
            copy(runtime/'apps/portable_runtime'/name, root/'.fleet-runtime'/name)
        execution = definition(manifest, platform)
        execution['components'][0]['configuration'] = {
            'values': {'model_management': {'required': True}},
            'credentials': {name: {'required': name != 'hub'} for name in ('hub', 'fleet', 'controller')}}
        execution['hooks']['before_stop']['component'] = 'backend'
        (root/'fleet.json').write_text(json.dumps(execution, indent=2)+'\n')
        (root/'README.md').write_text('''# Model Services management

Supply values.model_management.bus (auth and optional ca_pem), optional
hub_ca_pem/controller_ca_pem and endpoint-bound hub/fleet/controller credentials
through ordinary node-vault delivery. Credentials stay on this App; consumers
receive method-scoped grants. No ambient login, Fleet key or external Connector
checkout is used. This package carries the original model catalogs and Connector
sources, with original engines, configuration and lifecycle behavior.

The nine public management tools preserve their schemas. Team-local model
selection remains in Pantheon-Agent using each member's existing model access.
Private deployment plans survive restart. Shutdown drains local engine tasks
before closing connections; it does not stop deployed models or paid nodes.
Use explicit stop operations or the existing remote expiry policy for those.
Prepared credentials are renewed by deployment/restart; automatic renewal is
not implemented here. With no directory_root, the Hub credential is required and
the original Hub directory is used. For a standalone local profile, supply
values.model_management.directory_root pointing to its existing owner-bound
model directory, also used by inference. This App never initializes that directory.
In local mode Hub credentials are optional and authorize only Modal launch and
inventory operations; model publications and routes remain local. Without them,
Modal is explicitly unavailable, not an observed empty inventory. Local model
groups and automatic idle wake are not yet implemented.
''')
        build_artifact(root)
        root.rename(destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    build_package(args.output, args.platform)
