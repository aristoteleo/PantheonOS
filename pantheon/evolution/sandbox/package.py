"""Build the isolated mutation tools as an ordinary versioned Python App.

Only reviewed runtime files enter the package; no checkout, private settings,
Agent implementation or inference SDK is copied. Source and evaluator inputs
are supplied through initialize, independently of the immutable release digest.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile

from pantheon.apps.portable import execution_package
from pantheon.apps.schema import parse_manifest


RUNTIME_FILES = '''toolset.py settings.py constant.py
utils/log.py utils/misc.py utils/file_paths.py utils/vision.py utils/start_hook.py utils/owned_io.py
apps/toolset_backend.py internal/package_runtime/context.py remote/backend/base.py
platform/registry_lock.py
evolution/config.py evolution/program.py evolution/evaluator.py evolution/lifetime.py
evolution/local_shell.py evolution/utils/diff.py evolution/utils/metrics.py
evolution/sandbox/tool_backend.py evolution/sandbox/app.py'''.split()
APP_FILES = '''file/__init__.py file/file_manager.py file/apply_patch.py file/grep_glob.py
file/tree_sitter_parser.py file/image_sources.py
python/__init__.py python/python_interpreter.py
notebook/jupyter_kernel.py notebook/widgets.py notebook/python_environments.py
fleet/local_node.py desktop/app_runtime.py'''.split()

SIGNATURES = {
    'initialize': [('parent_files', 'dict', True, None), ('evaluator_code', 'str', True, None),
                   ('objective', 'str', True, None), ('timeout', 'int', False, 600),
                   ('inspirations', 'list | None', False, None), ('function_weight', 'float', False, 1),
                   ('evaluation_timeout', 'int | None', False, None)],
    'describe': [], 'evaluate_initial': [],
    'invoke_tool': [('provider', 'str', True, None), ('name', 'str', True, None), ('args', 'dict', True, None)],
    'finish': [('error', 'str', False, '')],
}


def build_package(destination, *, version='0.1.0', platform='linux-amd64'):
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Supply an App release version')
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    source = Path(__file__).resolve().parents[2]
    apps = source.parent / 'apps'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.mutation-tools-', dir=destination.parent) as temporary:
        root = Path(temporary) / 'source'
        vendor = root / 'backend/_vendor/pantheon'
        def copy(path, target):
            if path.is_symlink() or not path.is_file():
                raise ValueError('App release inputs must be regular files')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        for name in RUNTIME_FILES:
            copy(source / name, vendor / name)
        for name in APP_FILES:
            copy(apps / name, vendor / 'apps/builtin' / name)
        for path in (source / 'funcdesc').rglob('*.py'):
            if '__pycache__' not in path.parts:
                copy(path, vendor / path.relative_to(source))
        # Empty package roots avoid importing unrelated App/Agent implementations.
        for directory in [vendor, *[p for p in vendor.rglob('*') if p.is_dir()]]:
            init = directory / '__init__.py'
            if not init.exists():
                init.write_text('')
        (root / 'backend/__init__.py').write_text('from pantheon.evolution.sandbox.app import register\n')
        copy(Path(__file__).with_name('requirements.lock'), root / 'requirements.txt')
        manifest = {'apiVersion': 2, 'id': 'evolution-tools', 'name': 'Evolution Tools',
            'version': version, 'kind': 'service', 'surface': 'headless', 'runtime': 'process',
            'entry': {'backend': 'backend/__init__.py'},
            'description': 'Single-use isolated Files, Shell, Python and evaluation workspace.',
            'provides': {'interfaces': [{'name': 'isolated-mutation', 'version': 1,
                                        'tools': list(SIGNATURES)}],
                         'tools': [{'name': name, 'params': [
                             {'name': key, 'type': kind, 'required': required, **({} if required else {'default': default})}
                             for key, kind, required, default in params]}
                             for name, params in SIGNATURES.items()]},
            'execution': {'protocol': 1, 'manifest': 'fleet.json'}}
        parse_manifest(manifest)
        (root / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (root / 'README.md').write_text(
            '# Evolution Tools\n\nPlace this App inside an isolation boundary. '
            'Initialize once with parent_files, evaluator_code, objective and a finite timeout. '
            'The code and evaluator run inside that boundary. Source inputs are not release files. '
            'A controller outside the container owns durable operation receipts; a lost response '
            'must not be replayed. Stop confirmation belongs to the deployment owner. '
            'No model or controller credentials are required. Model-assisted image/sampling '
            'bindings are not yet composed by this package; do not treat this as full product parity.\n')
        with execution_package(root, platform) as prepared:
            shutil.copytree(prepared, Path(temporary) / 'release')
        (Path(temporary) / 'release').rename(destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--platform', default='linux-amd64')
    args = parser.parse_args()
    build_package(args.output, version=args.version, platform=args.platform)
