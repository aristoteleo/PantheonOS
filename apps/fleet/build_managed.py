"""Package the complete Fleet management toolset as an ordinary prepared App."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

RUNTIME_FILES = '''toolset.py utils/log.py utils/misc.py
internal/package_runtime/context.py remote/backend/base.py
appkit/runtime_config.py appkit/owned_bus.py appkit/fleet_controller.py appkit/toolset_backend.py appkit/resolver.py
appkit/client.py appkit/registry.py appkit/reflect.py appkit/schema.py appkit/distribution.py
appkit/lifecycle.py appkit/portable.py appkit/store_release.py appkit/versioning.py
appkit/compat.py appkit/dependency_assembly.py appkit/owner_journal.py
utils/registry_lock.py models/group_network.py models/group_overlay.py appkit/spec.py'''.split()


def build(output, platform):
    from pantheonos.appkit.portable import definition
    from pantheonos.appkit.reflect import reflect_toolset_class
    from pantheonos.appkit.schema import parse_manifest
    from .fleet import FleetToolSet
    if platform not in ('darwin-arm64', 'darwin-amd64', 'linux-arm64', 'linux-amd64'):
        raise ValueError('Prepared Fleet management currently requires a POSIX host')
    source = Path(__file__).resolve().parent
    from pantheonos.appkit.vendoring import add_package_inits, builtin_app_dir, runtime_path, vendor_file, vendor_tree
    from pantheonos.appkit.vendoring import PLATFORM_ROOT as runtime
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.fleet-build-', dir=output.parent) as temp:
        root = Path(temp)/'release'; root.mkdir()
        tools = [item.model_dump(exclude_none=True) for item in reflect_toolset_class(FleetToolSet)]
        manifest = {'apiVersion': 2, 'id': 'fleet', 'name': 'Fleet', 'version': '0.8.1',
            'runtime': 'process', 'surface': 'headless', 'kind': 'service',
            'description': 'Fleet node management, execution, transfers and HPC operations.',
            'entry': {'backend': 'backend/__init__.py'},
            'execution': {'protocol': 1, 'manifest': 'fleet.json'},
            'provides': {'tools': tools, 'interfaces': [{'name': 'fleet-management', 'version': 1,
                                                       'tools': [item['name'] for item in tools]}]}}
        parse_manifest(manifest)
        (root/'app.json').write_text(json.dumps(manifest, indent=2)+'\n')
        vendor = root / 'backend/_vendor'
        def copy(origin, target):
            if origin.is_symlink() or not origin.is_file():
                raise ValueError('Fleet release input must be a regular file')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        for name in RUNTIME_FILES:
            vendor_file(name, vendor)
        vendor_tree('funcdesc', vendor)
        for name in ('fleet.py', 'managed.py', 'inventory.py', 'hpc.py', 'update.py'):
            copy(source/name, builtin_app_dir(vendor) / 'fleet' / name)
        # Manifests of the node services this App starts on demand (files and
        # terminals); their code is the node's own installation.
        for app in ('file', 'node_files', 'pty'):
            copy(source.parent/app/'app.json', builtin_app_dir(vendor) / app/'app.json')
        add_package_inits(vendor)
        (root/'backend/__init__.py').write_text('from pantheonos.appkit.builtin.fleet.managed import register\n')
        (root/'requirements.txt').write_text('loguru==0.7.3\nrich==14.3.2\npydantic==2.12.5\nnats-py[nkeys,aiohttp]==2.16.0\nhttpx==0.28.1\n')
        copy(source.parent/'desktop/app_runtime.py', root/'.fleet-runtime/app_runtime.py')
        for name in ('host.py', 'install.py', 'launch.py'):
            copy(runtime/'appkit/portable_runtime'/name, root/'.fleet-runtime'/name)
        execution = definition(manifest, platform)
        execution['components'][0]['configuration'] = {'values': {'fleet': {'required': True}},
            'credentials': {'fleet': {'required': True}, 'controller': {'required': True}}}
        execution['hooks']['before_stop']['component'] = 'backend'
        (root/'fleet.json').write_text(json.dumps(execution, indent=2)+'\n')
        (root/'README.md').write_text('# Prepared Fleet management\n\n'
            'The complete original Fleet toolset runs with a private Fleet bus and Controller client. '
            'Supply values.fleet.bus (auth and optional ca_pem), optional controller_ca_pem, and '
            'endpoint-bound fleet and controller credentials from the node vault. '
            'Agents receive method-scoped grants, never these management credentials. '
            'HPC, updates, node execution and transfers reuse the original implementations. '
            'The local node is the hosting node from the prepared snapshot. '
            'Disconnect never discovers another user or ambient Fleet. Restart with fresh '
            'prepared credentials after expiry; automatic renewal remains a deployment concern. '
            'Shutdown joins transfer workers before closing its connections.\n')
        root.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    build(args.output, args.platform)
