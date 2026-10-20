"""Package the complete Notebook engine and existing GUI for ordinary Fleet startup."""
import argparse
import inspect
import json
from pathlib import Path
import shutil
import tempfile


def build(output: Path, platform: str, *, frontend: Path | None = None):
    from pantheonos.appkit.portable import definition
    from pantheonos.appkit.schema import parse_manifest
    from .managed import ManagedNotebook
    source = Path(__file__).resolve().parent
    from pantheonos.appkit.vendoring import add_package_inits, builtin_app_dir, vendor_file, vendor_tree
    from pantheonos.appkit.vendoring import PLATFORM_ROOT as runtime
    frontend = Path(frontend).resolve() if frontend is not None else source / 'frontend'
    if not (frontend / 'index.js').is_file():
        raise ValueError('Notebook frontend must contain the built index.js and its sibling assets')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.notebook-build-', dir=output.parent) as temp:
        package = Path(temp) / 'package'
        package.mkdir()
        manifest = json.loads((source / 'app.json').read_text())
        manifest.update(version='0.7.2', runtime='process',
                        execution={'protocol': 1, 'manifest': 'fleet.json',
                                   'rpc_transport': 'fleet', 'fs_transport': 'fleet'})
        manifest['entry'] = {'frontend': manifest['entry']['frontend'], 'backend': 'backend/__init__.py'}
        service = ManagedNotebook('manifest', streaming_mode='local', execution_logging=False)
        declared = {t['name'] for t in manifest['provides']['tools']}
        if service.functions.keys() != declared | {'execution_host'}:
            raise ValueError('Notebook manifest differs from its engine methods')
        for method in manifest['provides']['tools']:
            parameters = inspect.signature(service.functions[method['name']][0]).parameters
            for parameter in method.get('params', []):
                default = parameters[parameter['name']].default
                parameter['required'] = default is inspect.Parameter.empty
                if parameter['required']:
                    parameter.pop('default', None)
                else:
                    parameter['default'] = default
        manifest['provides']['tools'].append({'name': 'execution_host',
            'description': 'Describe the notebook execution node.', 'params': [], 'hidden': True})
        manifest['provides']['interfaces'] = [
            {'name': 'notebook', 'version': 1, 'tools': sorted(declared | {'execution_host'})}]
        manifest['notes'] = 'Shared workspace Notebook engine and GUI with explicit App configuration and owned lifecycle.'
        parse_manifest(manifest)
        (package / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
        shutil.copytree(frontend, package / 'frontend')
        # The ordinary App action adapter is runtime-owned; the viewer and all
        # lazy assets come from the explicitly selected frontend build.
        shutil.copyfile(source / 'frontend/control.js', package / 'frontend/control.js')
        vendor = package / 'backend/_vendor'
        for name in ('toolset.py', 'utils/log.py', 'utils/misc.py', 'appkit/runtime_config.py',
                     'appkit/toolset_backend.py', 'internal/package_runtime/context.py', 'remote/backend/base.py'):
            vendor_file(name, vendor)
        vendor_tree('funcdesc', vendor)
        notebook = builtin_app_dir(vendor) / 'notebook'
        notebook.mkdir(parents=True)
        for file in source.glob('*.py'):
            if file.name != 'build_managed.py':
                shutil.copyfile(file, notebook / file.name)
        add_package_inits(vendor)
        (package / 'backend/__init__.py').write_text('from pantheonos.appkit.builtin.notebook.managed import register\n')
        (package / 'requirements.txt').write_text(
            'jupyter-client==8.8.0\nipykernel==7.1.0\nipywidgets==8.1.9\nipycanvas==0.14.3\n'
            'nbformat==5.10.4\njedi==0.19.2\nloguru==0.7.3\nrich==14.3.2\npydantic==2.12.5\npsutil==7.2.2\n')
        adapter = package / '.fleet-runtime'
        adapter.mkdir()
        shutil.copyfile(source.parent / 'desktop/app_runtime.py', adapter / 'app_runtime.py')
        for name in ('host.py', 'install.py', 'launch.py'):
            shutil.copyfile(runtime / 'appkit/portable_runtime' / name, adapter / name)
        # A managed package bypasses portable.execution_package's adapter build.
        # Ship its ordinary SDK host too: Desktop loads /app-host.html from the
        # isolated App origin, not from the Atrium origin or an Agent service.
        shutil.copytree(runtime / 'appkit/portable_runtime/assets', adapter / 'assets')
        execution = definition(manifest, platform)
        execution['components'][0]['configuration'] = {'values': {'notebook': {'required': True}}}
        execution['hooks']['before_stop']['component'] = 'backend'
        (package / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
        (package / 'README.md').write_text(
            '# Notebook App\n\nThe original notebook engine and GUI run on the selected Fleet node. '
            'Supply values.notebook with execution_timeout (positive seconds) and execution_logging '
            '(boolean). Optional workspace selects an existing absolute project directory on the node; '
            'otherwise the ordinary Fleet host workspace is used. Logs and context metadata '
            'belong to App state; notebooks and selected kernels belong to the workspace. '
            'No model dependency is required for Python, widgets or notebook editing. '
            'Stopping closes admission and waits for accepted calls before saving and stopping kernels. '
            'Interrupt a long cell before requesting stop if you do not want to wait for its timeout. '
            'A persistence or kernel shutdown failure fails the stop hook. '
            'The legacy Notebook entry is unchanged.\n')
        package.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--frontend', type=Path, help='Directory produced by the paired UI notebook build')
    args = parser.parse_args()
    build(args.output, args.platform, frontend=args.frontend)
