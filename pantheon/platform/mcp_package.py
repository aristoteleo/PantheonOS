"""Build the existing MCP App with an explicit, versioned tool export contract.

Unlike the legacy gateway entry, this ordinary Fleet backend exposes only its
reviewed RPC tools. Server coordinates and credentials arrive at prepared start;
the package contains neither settings discovery nor Agent/runtime dependencies.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from pantheon.apps.portable import definition
from pantheon.apps.builtin.mcp.scoped import validate_exports


def build_package(destination, platform, *, exports, credential_slots=(), transport=None):
    if platform not in {f'{os}-{arch}' for os in ('linux', 'darwin', 'windows') for arch in ('amd64', 'arm64')}:
        raise ValueError('Unsupported MCP App platform')
    exports = validate_exports(exports)
    if (not isinstance(credential_slots, (list, tuple)) or len(credential_slots) > 16
            or any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', name)
                   for name in credential_slots)
            or len(set(credential_slots)) != len(credential_slots)):
        raise ValueError('Invalid MCP App credential slots')
    if transport is not None:
        from pantheon.models.package import transport_platform
        transport = Path(transport)
        if transport.is_symlink() or not transport.is_file() or transport_platform(transport) != platform:
            raise ValueError('Supply a regular target-platform Fleet workload transport')
    destination = Path(destination)
    source = Path(__file__).parents[1]
    destination.mkdir(parents=True, exist_ok=False)
    tools, functions = [], []
    for name, spec in exports.items():
        schema = spec['parameters']
        kinds = {'string': 'str', 'object': 'dict', 'array': 'list', 'boolean': 'bool',
                 'integer': 'int', 'number': 'float'}
        tools.append({'name': name, 'description': spec['description'], 'params': [
            {'name': key, 'type': kinds.get(value.get('type'), 'Any') if isinstance(value.get('type'), str) else 'Any',
             'required': key in schema.get('required', [])} for key, value in schema['properties'].items()]})
        functions.append({'name': name, 'description': spec['description'], 'parameters': schema})
    manifest = {'apiVersion': 2, 'id': 'mcp-gateway', 'name': 'MCP tools', 'version': '0.8.0',
        'kind': 'service', 'surface': 'headless', 'runtime': 'process',
        'entry': {'backend': 'backend/__init__.py'},
        'execution': {'protocol': 1, 'manifest': 'fleet.json'},
        'provides': {'tools': tools, 'interfaces': [{'name': 'mcp-tools', 'version': 1, 'tools': list(exports)}]}}
    (destination / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (destination / 'tool-functions.json').write_text(json.dumps(functions, indent=2) + '\n')
    backend = destination / 'backend'
    backend.mkdir()
    from pantheon.apps.builtin.mcp import scoped
    shutil.copyfile(scoped.__file__, backend / '__init__.py')
    shutil.copyfile(Path(scoped.__file__).with_name('sampling.py'), backend / 'sampling.py')
    (backend / 'exports.json').write_text(json.dumps(exports, indent=2) + '\n')
    vendor = backend / '_vendor' / 'pantheon' / 'apps'
    vendor.mkdir(parents=True)
    (vendor / '__init__.py').write_text('')
    (vendor.parent / '__init__.py').write_text('')
    shutil.copyfile(source / 'apps/runtime_config.py', vendor / 'runtime_config.py')
    from pantheon.models.package import bundle_client
    bundle_client(vendor.parent, platform=platform, transport=transport)
    (destination / 'requirements.txt').write_text('fastmcp==2.14.4\njsonschema==4.26.0\nhttpx==0.28.1\nloguru==0.7.3\n')
    adapter = destination / '.fleet-runtime'
    adapter.mkdir()
    from pantheon.apps.builtin.desktop import app_runtime
    shutil.copyfile(app_runtime.__file__, adapter / 'app_runtime.py')
    for name in ('host.py', 'install.py', 'launch.py'):
        shutil.copyfile(source / 'apps/portable_runtime' / name, adapter / name)
    execution = definition(manifest, platform)
    execution['components'][0]['configuration'] = {
        'values': {'mcp': {'required': True}},
        'credentials': {name: {'required': True} for name in credential_slots},
    }
    execution['hooks']['before_stop']['component'] = 'backend'
    (destination / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
    return destination


def validate_migration_contract(contract):
    """Validate captured provider views without creating a release or process."""
    from pantheon.apps.builtin.mcp.scoped import bounded, NAME
    contract = bounded(contract)
    if (not isinstance(contract, dict) or set(contract) != {'protocol', 'exports', 'providers'}
            or type(contract['protocol']) is not int or contract['protocol'] != 1
            or not isinstance(contract['providers'], dict) or not 1 <= len(contract['providers']) <= 64):
        raise ValueError('Supply a captured legacy MCP tool contract')
    exports = validate_exports(contract['exports'])
    if any(spec.get('result_format') != 'legacy-agent' for spec in exports.values()):
        raise ValueError('Migrated tools must preserve the legacy result contract')
    exposed = set()
    for name, functions in contract['providers'].items():
        if not NAME.fullmatch(name) or '__' in name:
            raise ValueError('Invalid captured MCP provider name')
        selected = [key for key in exports if name == 'mcp' or key.startswith(name + '_')]
        expected = [{'name': key, 'description': exports[key]['description'], 'strict': False,
                     'parameters': exports[key]['parameters']} for key in selected]
        if not selected or functions != expected:
            raise ValueError('MCP provider view differs from its captured export contract')
        exposed.update(selected)
    if exposed != set(exports):
        raise ValueError('MCP release includes exports outside its selected providers')
    return {**contract, 'exports': exports}


def build_migration_package(destination, platform, *, contract, credential_slots=(), transport=None):
    """Version captured gateway names and their per-Agent provider views together.

    This creates an ordinary package, not a deployment or an import receipt.
    Node placement, prepared server configuration, credentials and allocator
    grants must still be supplied by the owner-side composition.
    """
    contract = validate_migration_contract(contract)
    package = build_package(destination, platform, exports=contract['exports'],
                            credential_slots=credential_slots, transport=transport)
    (package/'migration-tools.json').write_text(json.dumps(contract, indent=2) + '\n')
    return package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    contract = parser.add_mutually_exclusive_group(required=True)
    contract.add_argument('--exports', type=Path)
    contract.add_argument('--legacy-catalog', type=Path,
                          help='Contract returned by the original MCP gateway export_migration_tools')
    parser.add_argument('--transport', type=Path)
    parser.add_argument('--credential-slot', action='append', default=[])
    args = parser.parse_args()
    if args.legacy_catalog:
        build_migration_package(args.output, args.platform,
            contract=json.loads(args.legacy_catalog.read_text()),
            credential_slots=args.credential_slot, transport=args.transport)
    else:
        build_package(args.output, args.platform, exports=json.loads(args.exports.read_text()),
                      credential_slots=args.credential_slot, transport=args.transport)


if __name__ == '__main__':
    main()
