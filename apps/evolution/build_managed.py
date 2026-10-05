"""Build the ordinary Evolution controller with an explicit Agent dependency."""
import argparse
import inspect
import json
from pathlib import Path
import shutil
import tempfile

# Reviewed controller sources; the Agent implementation and legacy sandbox
# launcher/worker are deliberately outside the independently shipped release.
CONTROLLER_FILES = '''apps/runtime_config.py apps/dependency_client.py
apps/agent_execution_client.py apps/agent_execution_runner.py
apps/modal_app_transport.py apps/modal_sandbox.py apps/modal_placement.py
apps/modal_credentials.py apps/modal_image.py
evolution/__init__.py evolution/database.py evolution/team.py
evolution/prompt_builder.py evolution/result.py evolution/visualizer.py
evolution/remote_execution.py evolution/remote_reasoning.py evolution/remote_run.py
evolution/sandbox/agent_execution.py evolution/sandbox/remote_operation.py'''.split()


def build(output, platform):
    from pantheon.apps.portable import definition
    from pantheon.apps.schema import parse_manifest
    from pantheon.evolution.sandbox.package import RUNTIME_FILES, APP_FILES
    from .evolution_toolset import EvolutionToolSet, EvolutionManager
    source = Path(__file__).resolve().parent
    runtime = source.parents[1] / 'pantheon'
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.evolution-build-', dir=output.parent) as temp:
        root = Path(temp) / 'release'
        root.mkdir()
        manifest = json.loads((source / 'app.json').read_text())
        manifest.update(version='0.7.0', runtime='process', execution={'protocol': 1, 'manifest': 'fleet.json'})
        manifest['entry'] = {'backend': 'backend/__init__.py'}
        manifest['dependencies'] = {'agent': {'uses': ['agent-execution@1']}}
        service = EvolutionToolSet('manifest', workdir=str(Path(temp) / 'state'), manager=EvolutionManager(Path(temp) / 'state'))
        declared = {t['name'] for t in manifest['provides']['tools']}
        if declared != service.functions.keys():
            raise ValueError('Evolution manifest differs from its public methods')
        for method in manifest['provides']['tools']:
            parameters = inspect.signature(service.functions[method['name']][0]).parameters
            for parameter in method.get('params', []):
                default = parameters[parameter['name']].default
                parameter['required'] = default is inspect.Parameter.empty
                if parameter['required']:
                    parameter.pop('default', None)
                else:
                    parameter['default'] = default
        manifest['provides']['interfaces'] = [{'name': 'evolution', 'version': 1, 'tools': sorted(declared)}]
        parse_manifest(manifest)
        (root / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
        vendor = root / 'backend/_vendor/pantheon'
        def copy(origin, target):
            if origin.is_symlink() or not origin.is_file():
                raise ValueError('App release input must be a regular file')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        # Tool implementations are needed for explicitly selected native-node
        # execution. Isolated execution instead invokes the separate tool App.
        for name in sorted(set(RUNTIME_FILES + CONTROLLER_FILES)):
            copy(runtime / name, vendor / name)
        for name in APP_FILES + ['web/__init__.py']:
            copy(source.parent / name, vendor / 'apps/builtin' / name)
        for name in ('__init__.py', 'evolution_toolset.py', 'evaluator_toolset.py', 'managed.py'):
            copy(source / name, vendor / 'apps/builtin/evolution' / name)
        for path in (runtime / 'funcdesc').rglob('*.py'):
            copy(path, vendor / path.relative_to(runtime))
        for directory in [vendor, *[p for p in vendor.rglob('*') if p.is_dir()]]:
            init = directory / '__init__.py'
            if not init.exists():
                init.write_text('')
        (root / 'backend/__init__.py').write_text('from pantheon.apps.builtin.evolution.managed import register\n')
        copy(source / 'requirements.lock', root / 'requirements.txt')
        adapter = root / '.fleet-runtime'
        copy(source.parent / 'desktop/app_runtime.py', adapter / 'app_runtime.py')
        for name in ('host.py', 'install.py', 'launch.py'):
            copy(runtime / 'apps/portable_runtime' / name, adapter / name)
        execution = definition(manifest, platform)
        execution['components'][0]['configuration'] = {
            'values': {'evolution': {'required': True}},
            'credentials': {'agent': {'required': True}, 'modal': {'required': False}}}
        execution['hooks']['before_stop']['component'] = 'backend'
        (root / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
        copy(source / 'MANAGED.md', root / 'README.md')
        root.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    build(args.output, args.platform)
