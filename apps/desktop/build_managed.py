"""Build a prepared Desktop backend candidate, independently of the Agent App."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile


RUNTIME_FILES = '''toolset.py utils/log.py utils/misc.py utils/file_paths.py
internal/package_runtime/context.py remote/backend/base.py
apps/runtime_config.py apps/owned_bus.py apps/toolset_backend.py apps/resolver.py apps/client.py
apps/lifecycle.py apps/portable.py apps/reflect.py apps/schema.py apps/registry.py
apps/distribution.py apps/store_release.py apps/versioning.py apps/compat.py
apps/dependency_assembly.py apps/owner_journal.py utils/registry_lock.py
models/group_network.py models/group_overlay.py apps/stream_runtime.py'''.split()


def build(output, platform, *, data_mode='loopback'):
    from pantheon.apps.portable import definition
    from pantheon.apps.reflect import reflect_toolset_class
    from pantheon.apps.schema import parse_manifest
    from .toolset import DesktopToolSet
    # Store and Desktop documents currently use POSIX file locks. Do not ship a
    # manifest claiming Windows support before the actual service can run there.
    if platform not in ('darwin-arm64', 'darwin-amd64', 'linux-arm64', 'linux-amd64'):
        raise ValueError('Prepared Desktop currently requires a macOS or Linux node')
    if data_mode not in ('loopback', 'tunnel'):
        raise ValueError('Desktop data mode must be loopback or tunnel')
    source = Path(__file__).resolve().parent
    runtime = source.parents[1] / 'pantheon'
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.desktop-build-', dir=output.parent) as temp:
        root = Path(temp) / 'release'
        root.mkdir()
        manifest = json.loads((source / 'app.json').read_text())
        manifest.update(version='0.15.0', runtime='process', execution={'protocol': 1, 'manifest': 'fleet.json'})
        manifest['entry'] = {'backend': 'backend/__init__.py'}
        # Preserve the full public/GUI tool face, including required parameters
        # which the historical manifest encoded with a sentinel default.
        tools = [tool.model_dump(exclude_none=True) for tool in reflect_toolset_class(DesktopToolSet)]
        manifest['provides']['tools'] = tools
        manifest['provides']['interfaces'] = [{'name': 'desktop', 'version': 1,
                                               'tools': [tool['name'] for tool in tools]}]
        parse_manifest(manifest)
        (root / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
        vendor = root / 'backend/_vendor/pantheon'
        def copy(origin, target):
            if origin.is_symlink() or not origin.is_file():
                raise ValueError('Desktop release input must be a regular file')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        for name in RUNTIME_FILES:
            copy(runtime / name, vendor / name)
        for path in (runtime / 'funcdesc').rglob('*.py'):
            copy(path, vendor / path.relative_to(runtime))
        for path in source.glob('*.py'):
            if path.name != 'build_managed.py':
                copy(path, vendor / 'apps/builtin/desktop' / path.name)
        for name in ('file/image_sources.py', 'fleet/inventory.py', 'qupath/native.py',
                     'qupath/bridge.py', 'qupath/bridge/startup.groovy'):
            copy(source.parent / name, vendor / 'apps/builtin' / name)
        # These canonical adapters stage ordinary Browser/native App artifacts;
        # the prepared Desktop does not install or launch a browser at startup.
        for directory in ('apps/portable_runtime', 'apps/native_stream'):
            for path in (runtime / directory).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                    copy(path, vendor / path.relative_to(runtime))
        for directory in [vendor, *[path for path in vendor.rglob('*') if path.is_dir()]]:
            init = directory / '__init__.py'
            if not init.exists():
                init.write_text('')
        (root / 'backend/__init__.py').write_text('from pantheon.apps.builtin.desktop.managed import register\n')
        copy(source / 'requirements.lock', root / 'requirements.txt')
        copy(source / 'app_runtime.py', root / '.fleet-runtime/app_runtime.py')
        for name in ('host.py', 'install.py', 'launch.py'):
            copy(runtime / 'apps/portable_runtime' / name, root / '.fleet-runtime' / name)
        execution = definition(manifest, platform)
        component = execution['components'][0]
        component['configuration'] = {
            'values': {'desktop': {'required': True}},
            'credentials': {'fleet': {'required': True}, 'events': {'required': True},
                            'store': {'required': False}, 'data': {'required': data_mode == 'tunnel'}}}
        if data_mode == 'tunnel':
            component['ports']['data'] = 0
        execution['hooks']['before_stop']['component'] = 'backend'
        (root / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
        copy(source / 'MANAGED.md', root / 'README.md')
        root.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--data-mode', choices=('loopback', 'tunnel'), default='loopback')
    args = parser.parse_args()
    build(args.output, args.platform, data_mode=args.data_mode)
