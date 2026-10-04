"""Owner-declared default Agent dependencies, independent of ambient settings.

This is deployment configuration, not authority: every selected name still needs
an approved profile and the ordinary per-instance allocator grant. It contains
no endpoints, credentials, discovery or MCP gateway expansion.
"""
from copy import deepcopy
import re


GROUPS = ('toolsets', 'mcp_servers')


def dependency_defaults(value, *, profiles=None):
    if (not isinstance(value, dict) or not set(GROUPS) <= value.keys()
            or value.keys() - {*GROUPS, 'mcp_unified_precedence'}
            or 'mcp_unified_precedence' in value and type(value['mcp_unified_precedence']) is not bool):
        raise ValueError('Supply explicit default toolset and MCP dependency lists')
    result = {}
    for group in GROUPS:
        names = value[group]
        if (not isinstance(names, list) or len(names) > 64
                or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', name)
                       or '__' in name for name in names)
                or len(set(names)) != len(names)
                or group == 'toolsets' and set(names) & {'think', 'task', 'mcp'}):
            raise ValueError('Invalid default dependency names; declare MCP names in mcp_servers')
        if profiles is not None and (not isinstance(profiles, dict)
                or not isinstance(profiles.get(group), dict) or set(names) - profiles[group].keys()):
            raise ValueError('Default dependencies require approved tool profiles')
        result[group] = list(names)
    if set(result['toolsets']) & set(result['mcp_servers']):
        raise ValueError('Default toolset and MCP dependency names conflict')
    if 'mcp_unified_precedence' in value:
        result['mcp_unified_precedence'] = value['mcp_unified_precedence']
    return result


def with_dependency_defaults(config, defaults):
    """Apply a validated snapshot to a canonical recipe before revision hashing.

    Keep explicit declarations first and never mutate the saved/template recipe.
    An empty selection in a recipe cannot remove deployment-required dependencies.
    Normal declarations retain named providers. An explicit migration policy can
    preserve the old factory's unified-gateway precedence, without rewriting the
    user's saved recipes or consulting ambient settings.
    """
    result = deepcopy(config)
    for name in defaults['toolsets']:
        if name not in result['toolsets']:
            result['toolsets'].append(name)
    mcps = set(result['mcp_servers'])
    mcps.update('mcp' if name == 'mcp' else name[4:] for name in result['toolsets']
                if name == 'mcp' or name.startswith('mcp:'))
    for name in defaults['mcp_servers']:
        if name not in mcps:
            result['mcp_servers'].append(name)
            mcps.add(name)
    if defaults.get('mcp_unified_precedence') and 'mcp' in mcps:
        result['toolsets'] = [name for name in result['toolsets']
                             if name != 'mcp' and not name.startswith('mcp:')]
        result['mcp_servers'] = ['mcp']
    return result
