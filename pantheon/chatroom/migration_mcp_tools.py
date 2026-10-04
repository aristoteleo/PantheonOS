"""Compile an observed legacy MCP catalog into ordinary App tool contracts.

No endpoint, command, credential or server placement is inferred here. Those
remain prepared deployment inputs. The catalog preserves gateway names and the
legacy provider's prefix filtering; it is not migration/import admission.
"""
import asyncio
from copy import deepcopy

from pantheon.apps.builtin.mcp.scoped import NAME, bounded, validate_exports


def _catalog(tools):
    if not isinstance(tools, list) or len(tools) > 64:
        raise ValueError('MCP catalog exceeds the supported App contract')
    result = {}
    for tool in tools:
        if (not isinstance(tool, dict) or set(tool) != {'name', 'description', 'parameters'}
                or not isinstance(tool['name'], str) or not tool['name']
                or tool['name'] in result or not isinstance(tool['description'], str)
                or not isinstance(tool['parameters'], dict)):
            raise ValueError('Invalid or ambiguous MCP tool catalog')
        result[tool['name']] = tool
    return result


def compile_mcp_tools(gateway_tools, servers, *, providers):
    """Return exports and per-provider functions for the ordinary allocator.

    servers maps manager names to {prefix, tools}. Both tool lists must come
    from the same gateway observation. Match exact names and schemas, not a
    longest-prefix guess (a_b/c and a/b_c can otherwise collide).
    """
    gateway_tools, servers, providers = bounded([gateway_tools, servers, providers])
    gateway = _catalog(gateway_tools)
    if (not isinstance(servers, dict) or not 1 <= len(servers) <= 64
            or not isinstance(providers, list) or not 1 <= len(providers) <= 64
            or any(not isinstance(p, str) or not NAME.fullmatch(p) or '__' in p for p in providers)
            or len(set(providers)) != len(providers)):
        raise ValueError('Select explicit legacy MCP providers')
    origins = {}
    for server, row in servers.items():
        if (not NAME.fullmatch(server) or not isinstance(row, dict)
                or set(row) != {'prefix', 'tools'} or not isinstance(row['prefix'], str)
                or not NAME.fullmatch(row['prefix'])):
            raise ValueError('Invalid MCP gateway mount')
        for raw_name, tool in _catalog(row['tools']).items():
            visible = row['prefix'] + '_' + raw_name
            if visible in origins:
                raise ValueError('Ambiguous MCP gateway tool ownership')
            origins[visible] = (server, tool)
    names = {p: [name for name in gateway if p == 'mcp' or name.startswith(p + '_')]
             for p in providers}
    if any(not selection for selection in names.values()):
        raise ValueError('A selected legacy MCP provider exposes no tools')
    selected = set().union(*map(set, names.values()))
    exports = {}
    for name in gateway:
        if name not in selected:
            continue
        if name not in origins:
            raise ValueError('A gateway tool has no captured source server')
        server, original = origins[name]
        if gateway[name]['parameters'] != original['parameters']:
            raise ValueError('MCP gateway and server schemas changed during capture')
        exports[name] = {'server': server, 'tool': original['name'],
                         'description': gateway[name]['description'],
                         'parameters': deepcopy(gateway[name]['parameters']),
                         'result_format': 'legacy-agent'}
    exports = validate_exports(exports)
    functions = {p: [{'name': name, 'description': exports[name]['description'], 'strict': False,
                     'parameters': deepcopy(exports[name]['parameters'])} for name in selected_names]
                 for p, selected_names in names.items()}
    return bounded({'protocol': 1, 'exports': exports, 'providers': functions})


async def capture_mcp_tools(manager, *, providers):
    """Observe catalogs under the legacy lifecycle lock; never invoke a tool.

    The caller must own manager._lock. A changed/ambiguous catalog is rejected,
    not partially published. Metadata reads can connect the existing MCP clients.
    Returned data has no server coordinates or launch environment.
    """
    from fastmcp import Client
    from pantheon.apps.builtin.mcp.manager import MCPManager
    if not isinstance(manager, MCPManager) or not manager._lock.locked():
        raise ValueError('Capture through the original locked MCP gateway')
    gateway = manager._gateway
    if gateway._unified_mcp is None:
        raise ValueError('Original MCP gateway has not been initialized')

    def rows(tools):
        return [{'name': t.name, 'description': t.description or '', 'parameters': t.inputSchema}
                for t in tools]

    async def collect():
        # Lock ordering matches the legacy manager's mount/stop operations.
        async with gateway._lock:
            servers = {}
            for name, instance in manager.instances.items():
                prefix = instance.config.mount_prefix or name
                mounted = gateway._mounted_servers.get(prefix)
                if mounted is None:
                    continue
                if instance.status != 'running' or mounted.status != 'healthy':
                    raise ValueError('MCP catalog includes an unavailable server')
                async with Client(mounted.server) as client:
                    tools = rows(await client.list_tools())
                servers[name] = {'prefix': prefix, 'tools': tools}
            async with Client(gateway._unified_mcp) as client:
                tools = rows(await client.list_tools())
            return compile_mcp_tools(tools, servers, providers=providers)
    try:
        return await asyncio.wait_for(collect(), timeout=30)
    except Exception:
        raise ValueError('Could not capture a consistent MCP tool contract') from None
