"""Owner-declared default Agent dependencies, independent of ambient settings.

This is deployment configuration, not authority: every selected name still needs
an approved profile and the ordinary per-instance allocator grant. It contains
no endpoints, credentials, discovery or MCP gateway expansion.
"""
from copy import deepcopy
import re


GROUPS = ('toolsets', 'mcp_servers')


def dependency_defaults(value, *, profiles=None):
    if not isinstance(value, dict) or set(value) != set(GROUPS):
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
    return result


def with_dependency_defaults(config, defaults):
    """Apply a validated snapshot to a canonical recipe before revision hashing.

    Keep explicit declarations first and never mutate the saved/template recipe.
    An empty selection in a recipe cannot remove deployment-required dependencies.
    No named MCP provider is discarded in favour of a supposed unified gateway:
    only the reviewed profiles define which tools each provider actually exposes.
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
    return result
