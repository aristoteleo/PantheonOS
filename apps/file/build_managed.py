"""Package existing filesystem operations as an independent native Fleet App.

No Agent, settings discovery, model SDK, Hub client or remote ToolSet bus is
included. Legacy builtins remain unchanged; this is an opt-in Files package.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile


def build(output: Path, platform: str):
    from pantheon.apps.portable import definition
    from pantheon.apps.schema import parse_manifest
    from pantheon.apps.builtin.file.managed import METHODS
    source = Path(__file__).resolve().parent
    runtime = source.parents[1] / 'pantheon'
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.files-build-', dir=output.parent) as temp:
        package = Path(temp) / 'package'
        package.mkdir()
        manifest = json.loads((source / 'app.json').read_text())
        manifest.update(version='0.6.9', runtime='process', surface='headless',
                        execution={'protocol': 1, 'manifest': 'fleet.json'})
        manifest['entry'] = {'backend': 'backend/__init__.py'}
        manifest['provides']['tools'] = [t for t in manifest['provides']['tools'] if t['name'] in METHODS]
        manifest['notes'] = 'Prepared shared filesystem service. Model-assisted legacy tools require separate App dependencies.'
        parse_manifest(manifest)
        (package / 'app.json').write_text(json.dumps(manifest, indent=2)+'\n')
        vendor = package / 'backend/_vendor/pantheon'
        modules = ('toolset.py', 'utils/log.py', 'utils/misc.py', 'utils/file_paths.py',
                   'apps/runtime_config.py', 'apps/toolset_backend.py',
                   'internal/package_runtime/context.py', 'remote/backend/base.py')
        for name in modules:
            dest = vendor / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(runtime / name, dest)
        shutil.copytree(runtime / 'funcdesc', vendor / 'funcdesc', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('file_manager.py','apply_patch.py','grep_glob.py','tree_sitter_parser.py','managed.py'):
            dest = vendor / 'apps/builtin/file' / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
        dest = vendor / 'apps/builtin/fleet/local_node.py'; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.parent / 'fleet/local_node.py', dest)
        for name in ('', 'utils', 'apps', 'apps/builtin', 'apps/builtin/file', 'apps/builtin/fleet',
                     'internal', 'internal/package_runtime', 'remote', 'remote/backend'):
            (vendor / name / '__init__.py').write_text('')
        (package / 'backend/__init__.py').write_text('from pantheon.apps.builtin.file.managed import register\n')
        (package / 'requirements.txt').write_text('loguru==0.7.3\nrich==14.3.2\npydantic==2.12.5\n'
            'diff-match-patch==20241021\ntree-sitter==0.25.2\ntree-sitter-python==0.25.0\ntree-sitter-javascript==0.25.0\n')
        adapter = package / '.fleet-runtime'; adapter.mkdir()
        shutil.copyfile(source.parent / 'desktop/app_runtime.py', adapter / 'app_runtime.py')
        for name in ('host.py','install.py','launch.py'):
            shutil.copyfile(runtime / 'apps/portable_runtime' / name, adapter / name)
        execution = definition(manifest, platform)
        execution['components'][0]['configuration'] = {'values': {'files': {'required': True}}}
        execution['hooks']['before_stop']['component'] = 'backend'
        (package / 'fleet.json').write_text(json.dumps(execution, indent=2)+'\n')
        shutil.copyfile(source / 'README.md', package / 'README.md')
        package.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    build(args.output, args.platform)
