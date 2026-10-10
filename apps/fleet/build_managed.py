"""Package the complete Fleet management toolset as an ordinary prepared App."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

RUNTIME_FILES = '''toolset.py utils/log.py utils/misc.py
internal/package_runtime/context.py remote/backend/base.py
apps/runtime_config.py apps/owned_bus.py apps/fleet_controller.py apps/toolset_backend.py apps/resolver.py
apps/client.py apps/registry.py apps/reflect.py apps/schema.py apps/distribution.py
apps/lifecycle.py apps/portable.py apps/store_release.py apps/versioning.py
apps/compat.py apps/dependency_assembly.py apps/owner_journal.py
utils/registry_lock.py models/group_network.py models/group_overlay.py apps/spec.py'''.split()


def build(output, platform):
    from pantheon.apps.portable import definition
    from pantheon.apps.reflect import reflect_toolset_class
    from pantheon.apps.schema import parse_manifest
    from .fleet import FleetToolSet
    if platform not in ('darwin-arm64', 'darwin-amd64', 'linux-arm64', 'linux-amd64'):
        raise ValueError('Prepared Fleet management currently requires a POSIX host')
    source = Path(__file__).resolve().parent
    runtime = source.parents[1] / 'pantheon'
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
        vendor = root/'backend/_vendor/pantheon'
        def copy(origin, target):
            if origin.is_symlink() or not origin.is_file():
                raise ValueError('Fleet release input must be a regular file')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        for name in RUNTIME_FILES:
            copy(runtime/name, vendor/name)
        for path in (runtime/'funcdesc').rglob('*.py'):
            copy(path, vendor/path.relative_to(runtime))
        for name in ('fleet.py', 'managed.py', 'inventory.py', 'hpc.py', 'update.py'):
            copy(source/name, vendor/'apps/builtin/fleet'/name)
        # Manifests of the node services this App starts on demand (files and
        # terminals); their code is the node's own installation.
        for app in ('file', 'node_files', 'pty'):
            copy(source.parent/app/'app.json', vendor/'apps/builtin'/app/'app.json')
        for directory in [vendor, *[p for p in vendor.rglob('*') if p.is_dir()]]:
            init = directory/'__init__.py'
            if not init.exists(): init.write_text('')
        (root/'backend/__init__.py').write_text('from pantheon.apps.builtin.fleet.managed import register\n')
        (root/'requirements.txt').write_text('loguru==0.7.3\nrich==14.3.2\npydantic==2.12.5\nnats-py[nkeys,aiohttp]==2.16.0\nhttpx==0.28.1\n')
        copy(source.parent/'desktop/app_runtime.py', root/'.fleet-runtime/app_runtime.py')
        for name in ('host.py', 'install.py', 'launch.py'):
            copy(runtime/'apps/portable_runtime'/name, root/'.fleet-runtime'/name)
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
