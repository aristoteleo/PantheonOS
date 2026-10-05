"""Package the original Model Service Connector for prepared App deployment.

The same server, credential pipe and engine adapters serve inference. Only its
startup entrypoint changes; existing interactive Model Services installs remain
available. This builder runs on the owner side and adds no runtime dependency.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.registry import BUILTIN_ROOT


def build_package(destination, platform):
    source = Path(BUILTIN_ROOT) / 'model-service'
    manifest = json.loads((source / 'app.json').read_text())
    variant = manifest['execution']['platform_manifests'].get(platform)
    if variant is None:
        raise ValueError('Unsupported Model Service Connector platform')
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.model-connector-', dir=destination.parent) as temporary:
        package = Path(temporary) / 'package'
        shutil.copytree(source, package, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
        definition = json.loads((package / variant).read_text())
        manifest['version'] = definition['version'] = '0.1.25'
        manifest['execution'] = {'protocol': 1, 'manifest': 'fleet.json'}
        manifest.update(kind='service', surface='headless')
        component = definition['components'][0]
        component['argv'] = [arg.replace('${PACKAGE}/server.py', '${PACKAGE}/prepared.py')
                             for arg in component['argv']]
        component['configuration'] = {'values': {'connector': {'required': True}}}
        # Readiness/drain retain the original server's process identity checks.
        definition['hooks']['before_stop']['component'] = 'backend'
        for path in package.glob('fleet.*.json'):
            path.unlink()
        for name, value in (('app.json', manifest), ('fleet.json', definition)):
            (package / name).write_text(json.dumps(value, indent=2) + '\n')
        shutil.copyfile(Path(__file__).parents[1] / 'apps/runtime_config.py',
                        package / '_fleet_runtime_config.py')
        # Validate ordinary artifact bounds/link policy before publishing output.
        build_artifact(package)
        package.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    build_package(args.output, args.platform)


if __name__ == '__main__':
    main()
