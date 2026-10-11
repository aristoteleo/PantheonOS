"""Bridge Agent intents to a bounded, composition-supplied dependency capability.

No Fleet owner key or ambient discovery is available here. The capability selects
only approved aliases; the platform supplies sessions and gateway authorization.
Remote App composition supplies RemoteDependencyBindings over a prepared,
gateway-scoped allocator credential. Local compositions may pass
ScopedDependencyBindings directly. The composition owns capability shutdown.
"""
import asyncio
import re
import secrets

from pantheon.apps.dependency_assembly import _copy, _grant, _identity, CONFIGURATION_BYTES
from pantheon.apps.dependency_client import DependencyCallError, DependencyClient
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.instance_store import InstanceIntent
from pantheon.factory.instances import AgentInstanceBinding


class DependencyInstanceProvisioner:
    def __init__(self, capability, *, consumer, profiles, tls_context=None, owner=None, rpc_origin=None):
        self._consumer, self._profiles = _copy(consumer), _copy(profiles, CONFIGURATION_BYTES)
        _identity(self._consumer)
        if rpc_origin is not None:
            match = re.fullmatch(r'https://127\.0\.0\.1:([1-9][0-9]{0,4})', rpc_origin) if isinstance(rpc_origin, str) else None
            if not match or int(match[1]) > 65535 or tls_context is None:
                raise ValueError('Local dependency grants require an explicit loopback issuer and TLS trust')
        self._rpc_origin = rpc_origin
        if not callable(getattr(capability, 'bind', None)) or set(self._profiles) != {'toolsets', 'mcp_servers'}:
            raise ValueError('Supply an explicit dependency capability and tool profiles')
        for group in self._profiles.values():
            if not isinstance(group, dict):
                raise ValueError('Invalid dependency tool profiles')
            for name, profile in group.items():
                if (not isinstance(profile, dict) or not {'alias', 'functions'} <= profile.keys()
                        or profile.keys() - {'alias', 'functions', 'provider', 'service_functions'}
                        or not isinstance(profile['alias'], str) or not profile['alias']):
                    raise ValueError('Invalid dependency tool profile')
                if 'provider' in profile:
                    _identity(profile['provider'], provider=True)
                # Validate before allocating anything. No client or connection
                # is created just to validate these caller-visible schemas.
                functions = DependencyToolProvider._validate_functions(profile['functions'])
                if 'service_functions' in profile:
                    services = DependencyToolProvider._validate_functions(profile['service_functions'])
                    if functions.keys() & services.keys() or len(functions) + len(services) > 64:
                        raise ValueError('Invalid dependency service methods')
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', name) or '__' in name:
                    raise ValueError('Invalid dependency tool name')
        self._capability, self._tls_context, self._owner = capability, tls_context, owner
        self._allocated = {}
        # This process's allocations; a restart never replays an older grant.
        self._process = secrets.token_hex(6)

    async def bind(self, intent: InstanceIntent):
        if not isinstance(intent, InstanceIntent):
            raise ValueError('Supply a reserved Agent instance intent')
        selected = {'toolsets': {}, 'mcp_servers': {}}
        wanted = list(intent.config.get('mcp_servers', ()))
        for name in intent.config.get('toolsets', ()):
            if name in ('think', 'task'):
                continue
            if name == 'mcp':
                wanted.append('mcp')
            elif name.startswith('mcp:'):
                wanted.append(name[4:])
            else:
                if name not in self._profiles['toolsets']:
                    raise ValueError('Agent requested an unapproved dependency')
                selected['toolsets'][name] = self._profiles['toolsets'][name]
        for name in wanted:
            if name not in self._profiles['mcp_servers']:
                raise ValueError('Agent requested an unapproved MCP dependency')
            selected['mcp_servers'][name] = self._profiles['mcp_servers'][name]
        if selected['toolsets'].keys() & selected['mcp_servers'].keys():
            raise ValueError('Conflicting Agent dependency names')
        aliases = sorted({profile['alias'] for group in selected.values() for profile in group.values()})
        if not aliases:
            return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings())
        value = await self._capability.bind(owner_ref=intent.instance_id,
                                           operation_id=intent.operation_id, aliases=aliases)
        if (not isinstance(value, dict) or set(value) != {'protocol', 'owner_ref', 'operation_id', 'consumer', 'bindings'}
                or type(value['protocol']) is not int or value['protocol'] != 1
                or value['owner_ref'] != intent.instance_id or value['operation_id'] != intent.operation_id
                or value['consumer'] != self._consumer or not isinstance(value['bindings'], dict)
                or set(value['bindings']) != set(aliases)):
            raise ValueError('Dependency delivery does not match the Agent instance intent')
        created, groups = [], {}
        try:
            for kind, entries in selected.items():
                groups[kind] = {}
                for name, profile in entries.items():
                    tool = self._tool(name, profile, value['bindings'][profile['alias']])
                    created.append(tool)
                    groups[kind][name] = tool
            return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings(**groups))
        except BaseException:
            # Client shutdown drains its own accepted calls, never releases a
            # remote session that older config revisions may still be using.
            await asyncio.gather(*(tool.shutdown() for tool in created))
            raise

    def _tool(self, name, profile, grant):
        # Validate every returned bearer, endpoint and consumer.
        owner = grant['consumer']['fleet_id']
        provider = {k: v for k, v in grant['provider'].items() if k != 'fleet_id'}
        if ((self._owner is not None and owner != self._owner)
                or 'provider' in profile and provider != profile['provider']):
            raise ValueError('Dependency delivery does not match its approved provider')
        _identity(provider, provider=True)
        _grant(grant, {'consumer': self._consumer, 'provider': provider}, owner, rpc_origin=self._rpc_origin)
        client = DependencyClient(RuntimeCredential(grant['endpoint'], grant['access_token']),
                                  tls_context=self._tls_context)
        return DependencyToolProvider(name, client, profile['functions'],
                                      service_functions=profile.get('service_functions'))

    def allocated(self, name, *, owner_ref):
        """A toolset the Agent App itself uses (auxiliary work, GUI views),
        bound through the allocator on first use instead of at App start, so
        the Agent never waits for that provider to come up."""
        if name not in self._profiles['toolsets'] or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', owner_ref or ''):
            raise ValueError('Allocated toolsets need an approved profile and a logical owner')
        key = (owner_ref, name)
        if key not in self._allocated:
            self._allocated[key] = AllocatedToolProvider(self, name, owner_ref)
        return self._allocated[key]

    async def _allocate(self, name, owner_ref, attempt):
        profile = self._profiles['toolsets'][name]
        operation_id = f'{owner_ref}-{self._process}-{attempt}'
        value = await self._capability.bind(owner_ref=owner_ref, operation_id=operation_id,
                                           aliases=[profile['alias']])
        if (not isinstance(value, dict) or set(value) != {'protocol', 'owner_ref', 'operation_id', 'consumer', 'bindings'}
                or value['protocol'] != 1 or value['owner_ref'] != owner_ref or value['operation_id'] != operation_id
                or value['consumer'] != self._consumer or not isinstance(value['bindings'], dict)
                or set(value['bindings']) != {profile['alias']}):
            raise ValueError('Dependency delivery does not match the Agent App')
        return self._tool(name, profile, value['bindings'][profile['alias']])

    async def retire(self, instance_id):
        value = await self._capability.retire(owner_ref=instance_id)
        if (not isinstance(value, dict) or value.get('consumer') != self._consumer
                or value.get('owner_ref') != instance_id or type(value.get('protocol')) is not int or value['protocol'] != 1
                or value.get('state') not in {'retiring', 'retired'}):
            raise ValueError('Retirement delivery does not match the Agent instance')
        return value


# Provider errors after which the grant (or the provider instance it names) is
# gone: rebind instead of failing every later call.
_REBIND = {401, 403, 404, 410, 502, 503}


class AllocatedToolProvider(DependencyToolProvider):
    """A dependency toolset bound on first call and rebound when its grant is lost."""

    def __init__(self, provisioner, name, owner_ref):
        profile = provisioner._profiles['toolsets'][name]
        self.toolset_name = name
        self._provisioner, self._owner_ref = provisioner, owner_ref
        self._tools = self._validate_functions(profile['functions'])
        self._services = ({} if 'service_functions' not in profile
                          else self._validate_functions(profile['service_functions']))
        self._pending = set()
        self._closed = False
        self._bound = None
        self._attempt = 0
        self._lock = asyncio.Lock()

    async def _current(self):
        async with self._lock:
            self._check_open()
            if self._bound is None:
                # Each attempt is a new allocator operation: a lost grant is
                # never revived, the allocator issues a fresh one.
                self._attempt += 1
                self._bound = await self._provisioner._allocate(self.toolset_name, self._owner_ref,
                                                                self._attempt)
            return self._bound

    async def call_tool(self, name, args):
        self._check_open()
        if not isinstance(name, str) or name not in self._tools and name not in self._services:
            raise ValueError("Tool is not available in this dependency binding")
        bound = await self._current()
        try:
            return await bound.call_tool(name, args)
        except DependencyCallError as error:
            if error.status in _REBIND:
                async with self._lock:
                    if self._bound is bound:
                        self._bound = None
                        self._pending.add(asyncio.create_task(bound.shutdown()))
            raise

    async def shutdown(self):
        self._closed = True
        async with self._lock:
            bound, self._bound = self._bound, None
        if bound is not None:
            await bound.shutdown()
        await asyncio.gather(*tuple(self._pending), return_exceptions=True)
