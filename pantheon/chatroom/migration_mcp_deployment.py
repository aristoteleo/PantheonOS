"""Captured MCP tool views expressed as existing App/allocator declarations.

No new runtime routing or lifecycle: one ordinary MCP process supplies the old
gateway's shared state, with separate method grants for each selected provider.
"""
from pantheon.apps.dependency_assembly import AssemblyError, NAME, _copy, _matches
from pantheon.platform.mcp_package import validate_migration_contract


def dependency_inputs(contract, *, name, aliases):
    contract = validate_migration_contract(contract)
    providers = contract['providers']
    if (not _matches(NAME, name) or not isinstance(aliases, dict)
            or set(aliases) != set(providers) or not 1 <= len(aliases) <= 16
            or any(not _matches(NAME, alias) for alias in aliases.values())
            or len(set(aliases.values())) != len(aliases)):
        raise AssemblyError('Assign a distinct allocator alias to every captured MCP provider')
    tools, profiles = {}, {}
    for provider, functions in providers.items():
        alias = aliases[provider]
        tools[alias] = {'app_id': 'mcp-gateway',
            'provider': {'$app': name, 'component': 'backend', 'port': 'http'},
            'methods': {function['name']: {'arguments': list(function['parameters']['properties']), 'bound': {}}
                        for function in functions}}
        profiles[provider] = {'alias': alias, 'functions': functions}
    return _copy({'dependencies': {'mcp-gateway': {
        'range': '^0.8.0', 'uses': ['mcp-tools@1'], 'binding': 'runtime'}},
        'profiles': {'toolsets': {}, 'mcp_servers': profiles}, 'tools': tools})
