"""Local product distribution and Agent preset, using ordinary App composition.

The bundle contains immutable executables and App releases, never user settings
or credentials. The private setup is an explicit snapshot of the user's Agent,
tools and Model Services choices. Neither function imports an Agent runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import platform as host_platform
import shutil
import sys
import tempfile

from .agent_deployment import compose_deployment
from pantheonos.appkit.dependency_assembly import AssemblyError, _copy, _matches, NAME, DEPLOYMENT_BYTES
from pantheonos.appkit.lifecycle import build_artifact
from pantheonos.appkit.release_set import index_packages, _read_index, PLATFORM
from pantheonos.local.fleet import LocalFleetBinaries, PantheonBinary


INDEX = 'local-bundle.json'
# The owner (pantheonos.local) the bundle runs with: this package's source and
# its hash-pinned requirements, run by `pantheon up` in the managed Python.
OWNER = 'owner'
BINARIES = ('controller', 'broker', 'runner')
# Set by `pantheon up`: the pantheon binary runs the Fleet (all three), so the
# bundle needs no executables of its own.
FLEET_BINARY = 'PANTHEON_FLEET_BINARY'
CORE = {'agent': 'agent', 'allocator': 'dependency-binding', 'model-access': 'model-services-control'}


def native_platform():
    machine = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'amd64', 'AMD64': 'amd64'}.get(host_platform.machine())
    if sys.platform not in ('darwin', 'linux') or machine is None:
        raise AssemblyError('Local App products currently require macOS or Linux on arm64/amd64')
    return sys.platform + '-' + machine


def _relative(root, value):
    if not isinstance(value, str) or not value or value != Path(value).as_posix():
        raise AssemblyError('Bundle paths must be canonical relative paths')
    path = Path(value)
    if path.is_absolute() or any(part in ('.', '..') for part in path.parts):
        raise AssemblyError('Bundle paths must stay inside the distribution')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise AssemblyError('Bundle paths cannot follow symbolic links')
    return current


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _entries(root, target):
    result = {}
    for alias, variants in _read_index(root)['apps'].items():
        if not _matches(NAME, alias) or not isinstance(variants, dict):
            raise AssemblyError('Invalid product App aliases')
        entry = variants.get(target)
        if (not isinstance(entry, dict) or not {'path', 'app_id', 'version', 'revision', 'bytes'} <= set(entry)
                or set(entry) - {'path', 'app_id', 'version', 'revision', 'bytes', 'artifact', 'requires', 'prefer'}
                or not _matches(r'[a-f0-9]{64}', entry['revision'])
                or not isinstance(entry['app_id'], str) or not isinstance(entry['version'], str)
                or type(entry['bytes']) is not int or entry['bytes'] <= 0):
            raise AssemblyError('Every bundled App needs the selected native variant')
        result[alias] = (entry, _relative(root, entry['path']))
    if any(name not in result or result[name][0]['app_id'] != identity for name, identity in CORE.items()):
        raise AssemblyError('Bundle must contain the Agent, allocator and original model access Apps')
    return result


def build_bundle(destination, *, release, binaries, target):
    """Copy one verified release-set variant plus supplied Fleet executables
    (none when binaries is None: the pantheon binary runs the Fleet).

    This packages existing artifacts; it does not download/build an engine,
    execute an install hook, discover a daemon or modify an installed product.
    """
    destination, release = Path(destination).absolute(), Path(release).absolute()
    if not _matches(PLATFORM, target) or not target.startswith(('darwin-', 'linux-')):
        raise AssemblyError('Select an explicitly built POSIX product variant')
    if release.is_symlink(): raise AssemblyError('Use a real release directory')
    if destination.exists() or destination.is_symlink(): raise FileExistsError(destination)
    if binaries is not None:
        binaries.validate()
    entries = _entries(release, target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.local-product-', dir=destination.parent) as temporary:
        root = Path(temporary)/'bundle'
        (root/'apps').mkdir(parents=True)
        if binaries is not None:
            (root/'bin').mkdir()
        selected = {}
        for alias, (entry, source) in entries.items():
            # Verify the actual copied bytes, including platform declarations,
            # before publishing the bundle directory atomically.
            output = root/'apps'/alias
            shutil.copytree(source, output, symlinks=True,
                            ignore=shutil.ignore_patterns('.git', '__pycache__', 'node_modules', '.venv', '.env*'))
            payload, revision = build_artifact(output, target)
            if revision != entry['revision'] or len(payload) != entry['bytes']:
                raise AssemblyError('App release changed while composing the local product')
            selected[alias] = {target: output}
        index_packages(root/'apps', selected)
        _owner(root/OWNER)
        pinned = {}
        for name in BINARIES if binaries is not None else ():
            source = Path(getattr(binaries, name))
            output = root/'bin'/name
            shutil.copyfile(source, output)
            output.chmod(0o755)
            pinned[name] = {'path': 'bin/' + name, 'sha256': _sha(output)}
        (root/INDEX).write_text(json.dumps({'protocol': 1, 'platform': target, 'release': 'apps',
                                          'binaries': pinned, 'owner': OWNER}, sort_keys=True, indent=2) + '\n')
        root.rename(destination)
    return destination


def _owner(destination):
    """This pantheon package and the owner's requirements, for `pantheon up`.

    A source checkout keeps the first-party Apps outside the package
    (<repo>/apps, pantheonos/appkit/builtin/__init__.py); their runtime face is
    folded in as an installed package has it (setup.py).
    """
    source = Path(__file__).resolve().parents[1]
    skip = ('__pycache__', '*.pyc', '.env*', '.venv', 'node_modules', '.git', '.DS_Store')
    shutil.copytree(source, destination/'pantheonos', ignore=shutil.ignore_patterns(*skip))
    # The owner also serves the Agent's terminal frontend (pantheon agent chat).
    from pantheonos.appkit.vendoring import agent_package_dir
    agent = agent_package_dir()
    if agent is not None:
        shutil.copytree(agent, destination/'pantheon', ignore=shutil.ignore_patterns(*skip))
    apps = source.parent/'apps'
    if apps.is_dir():
        from pantheonos.appkit.distribution import STORE_APPS
        builtin = destination/'pantheonos'/'appkit'/'builtin'
        for app in sorted(apps.iterdir()):
            if app.is_dir() and app.name not in STORE_APPS:
                shutil.copytree(app, builtin/app.name, ignore=shutil.ignore_patterns(
                    *skip, '*.go', 'go.mod', 'go.sum', 'frontend-src'))
    shutil.copyfile(source/'local'/'owner-requirements.lock', destination/'requirements.txt')


def read_bundle(root, fleet_binary=None):
    """Resolve a product for this host without rebuilding installed App bytes.

    fleet_binary: the pantheon executable, which runs the Fleet in place of
    the bundle's own executables. Without it, a bundle that carries none
    resolves with no binaries (None).
    """
    root = Path(root).expanduser().absolute()
    if root.is_symlink(): raise AssemblyError('Use a real local product directory')
    path = _relative(root, INDEX)
    if path.stat().st_size > 65536: raise AssemblyError('Invalid local bundle index')
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) - {'owner'} != {'protocol', 'platform', 'release', 'binaries'}
            or value.get('owner', OWNER) != OWNER
            or type(value['protocol']) is not int or value['protocol'] != 1
            or value['platform'] != native_platform() or not isinstance(value['binaries'], dict)
            or set(value['binaries']) not in (set(BINARIES), set())):
        raise AssemblyError('Local bundle does not match this host or supported product protocol')
    paths = []
    for name in BINARIES if value['binaries'] and not fleet_binary else ():
        item = value['binaries'][name]
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256'}
                or not _matches(r'[a-f0-9]{64}', item['sha256'])):
            raise AssemblyError('Invalid bundled executable metadata')
        binary = _relative(root, item['path'])
        if not binary.is_file() or not os.access(binary, os.X_OK) or _sha(binary) != item['sha256']:
            raise AssemblyError('Bundled executable changed; rebuild or restore this product')
        paths.append(binary)
    release = _relative(root, value['release'])
    entries = _entries(release, value['platform'])
    if fleet_binary:
        binary = PantheonBinary(Path(fleet_binary))
        try:
            binary.validate()
        except ValueError as error:
            raise AssemblyError(str(error)) from None
        return binary, entries
    return (LocalFleetBinaries(*paths) if paths else None), entries


def bundle_release(root):
    """The bundle's release set (index and artifacts) the local controller deploys."""
    root = Path(root).expanduser().absolute()
    value = json.loads(_relative(root, INDEX).read_text())
    return _relative(root, value['release'])


def manifest(value):
    value = _copy(value, DEPLOYMENT_BYTES)
    if (not isinstance(value, dict) or set(value) != {'protocol', 'packages', 'apps', 'model_apps'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or not isinstance(value['packages'], dict) or not 1 <= len(value['packages']) <= 24
            or not isinstance(value['apps'], dict) or not 1 <= len(value['apps']) <= 16
            or not isinstance(value['model_apps'], dict) or len(value['model_apps']) > 8
            or value['apps'].keys() & value['model_apps'].keys()):
        raise AssemblyError('Supply a bounded local App profile manifest')
    for name, package in value['packages'].items():
        if (not _matches(NAME, name) or not isinstance(package, dict)
                or not {'path', 'revision'} <= package.keys() or package.keys() - {'path', 'revision', 'platform'}
                or 'platform' in package and not _matches(r'(darwin|linux)-(arm64|amd64)', package['platform'])
                or not isinstance(package['path'], str) or not Path(package['path']).is_absolute()
                or not _matches(r'[a-f0-9]{64}', package['revision'])):
            raise AssemblyError('Local packages need absolute paths and exact artifact digests')
    targets = dict(value['apps'])
    for name, item in value['model_apps'].items():
        if not isinstance(item, dict) or set(item) != {'app', 'deployment_id', 'name', 'models'}:
            raise AssemblyError('Supply explicit attached model publications')
        targets[name] = item['app']
    for name, app in targets.items():
        if (not _matches(NAME, name) or not isinstance(app, dict)
                or set(app) != {'package', 'scope', 'components', 'bindings'}
                or not isinstance(app['package'], str) or app['package'] not in value['packages']):
            raise AssemblyError('Local Apps need a declared package and ordinary configuration/bindings')
    if {app['package'] for app in targets.values()} != set(value['packages']):
        raise AssemblyError('Include exactly the packages used by this local profile')
    return value


def compose_profile(entries, setup):
    """Compile the owner setup into the existing deterministic profile format.

    The canonical Agent deployment preset checks declarations and constructs all
    policies/bindings. Template-only identities below are removed before return;
    LocalAppProfile supplies real owner/node/generations and its own vault refs.
    No user's settings, plugins, tools or model routes are disabled here.
    """
    value = _copy(setup, DEPLOYMENT_BYTES)
    if isinstance(value, dict) and 'preset' in value:
        from .agent_preset import expand_general_team
        value = expand_general_team(entries, value)
    required = {'protocol', 'agent', 'tools', 'models', 'providers', 'model_apps'}
    if (not isinstance(value, dict) or not required <= value.keys()
            or value.keys() - required - {'credentials', 'extra_bindings', 'tool_contracts'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or not isinstance(value['providers'], dict) or not isinstance(value['model_apps'], dict)
            or set(value['providers']) & (set(CORE) | set(value['model_apps']))
            or set(value['model_apps']) & set(CORE)):
        raise AssemblyError('Supply an explicit local Agent setup with model and tool selections')
    used = set(CORE) | set(value['providers']) | set(value['model_apps'])
    if not used <= entries.keys():
        raise AssemblyError('The product is missing a configured App; preserve its dependency declaration')
    # An owner can select a complete versioned App tool face instead of copying
    # schemas and permission rules into setup by hand. This never augments team
    # recipes or deployment defaults, nor substitutes an undeclared provider.
    from pantheonos.appkit.tool_profiles import compile_tool_profile
    contracts = value.get('tool_contracts', {})
    if not isinstance(contracts, dict):
        raise AssemblyError('Tool contracts must name explicit App selections')
    profiles = None
    if contracts:
        try:
            profiles = value['agent']['dependencies']['profiles']['toolsets']
            if not isinstance(profiles, dict) or not isinstance(value['tools'], dict):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise AssemblyError('Tool contracts require explicit Agent profiles and allocation policies') from None
    for name, contract in contracts.items():
        if (not _matches(r'[A-Za-z][A-Za-z0-9_]{0,127}', name) or '__' in name
                or name in {'task', 'think', 'mcp'} or not isinstance(contract, dict)
                or not {'app', 'uses'} <= contract.keys() or contract.keys() - {'app', 'uses', 'resource', 'service_methods'}
                or not isinstance(contract['app'], str)
                or contract['app'] not in value['providers'] and contract['app'] != 'agent'):
            # The Agent itself is a provider only of its pantheon_command.
            raise AssemblyError('Select an explicitly configured ordinary tool App')
        alias = contract['app']
        if name in profiles or alias in value['tools']:
            raise AssemblyError('A tool contract cannot replace an existing explicit profile or policy')
        source = json.loads((entries[alias][1] / 'app.json').read_text())
        if source.get('id') != entries[alias][0]['app_id']:
            raise AssemblyError('Tool contract identity differs from its selected release')
        profiles[name], value['tools'][alias], _ = compile_tool_profile(
            source, alias=alias, uses=contract['uses'], resource=contract.get('resource'),
            service_methods=contract.get('service_methods'))
    packages = {name: {'path': str(entries[name][1]), 'revision': entries[name][0]['revision'],
                       'platform': native_platform()} for name in used}
    targets = {name: {'node_id': 'local-template', 'revision': packages[name]['revision'],
                      'scope': name, 'generation': 0} for name in CORE}
    providers = {}
    for name, config in value['providers'].items():
        if not isinstance(config, dict) or set(config) != {'scope', 'components', 'bindings'}:
            raise AssemblyError('Local providers use ordinary components and bindings')
        providers[name] = {'node_id': 'local-template', 'revision': packages[name]['revision'],
                           'generation': 0, **config}
    # These are compiler markers, not an issued credential or live endpoint.
    marker = {'ref': 'node-secret://local-template', 'endpoint': 'https://local-template.invalid'}
    recipe = compose_deployment(owner='local-template', operation_id='local-template', targets=targets,
        agent=value['agent'], tools=value['tools'], models=value['models'],
        credentials={'agent': value.get('credentials', {}), 'allocator': {'hub': marker, 'controller': marker},
                     'model-access': {'hub': marker}}, provider_apps=providers,
        extra_bindings=value.get('extra_bindings', {}))
    apps = {name: {'package': name, 'scope': app['scope'], 'components': app['components'],
                   'bindings': app['bindings']} for name, app in recipe['apps'].items()}
    for name in ('allocator', 'model-access'):
        credentials = apps[name]['components']['backend']['credentials']
        for key in credentials: credentials[key] = {'$local': 'owner_credential'}
    backend = apps['agent']['components']['backend']['values']['agent']
    if {'rpc_origin', 'trust_roots_pem'} & backend.keys():
        raise AssemblyError('Local product trust is supplied by its profile, not copied from another runtime')
    backend.update(rpc_origin={'$local': 'controller'}, trust_roots_pem={'$local': 'trust_roots_pem'})
    allocator = apps['allocator']['components']['backend']['values']['dependency_binding']
    allocator.update(rpc_origin={'$local': 'controller'}, trust_roots_pem={'$local': 'trust_roots_pem'})
    access = apps['model-access']['components']['backend']['values']['model_services']
    access.update(http_origin={'$local': 'controller'}, trust_roots_pem={'$local': 'trust_roots_pem'},
                  directory_root={'$local': 'directory_root'})
    model_apps = {}
    for name, publication in value['model_apps'].items():
        if (not isinstance(publication, dict) or set(publication) != {'deployment_id', 'name', 'models', 'app'}
                or not isinstance(publication['app'], dict)
                or set(publication['app']) != {'scope', 'components', 'bindings'}):
            raise AssemblyError('Use ordinary attached Model Service publications in local setup')
        model_apps[name] = {**publication, 'app': {'package': name, **publication['app']}}
    return manifest({'protocol': 1, 'packages': packages, 'apps': apps, 'model_apps': model_apps})


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Package a local Agent product from an existing ordinary release set')
    for name in ('output', 'release'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in BINARIES:
        parser.add_argument('--' + name, type=Path,
                            help='Omit all three for a bundle the pantheon binary runs (`pantheon up`)')
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    given = [getattr(args, name) for name in BINARIES]
    if any(given) and not all(given):
        parser.error('Supply all of --controller, --broker and --runner, or none')
    build_bundle(args.output, release=args.release, target=args.platform,
                 binaries=LocalFleetBinaries(*(path.absolute() for path in given)) if all(given) else None)


if __name__ == '__main__':
    main()
