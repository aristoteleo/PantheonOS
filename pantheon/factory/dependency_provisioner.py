"""Bridge Agent intents to a bounded, composition-supplied dependency capability.

No Fleet owner key or ambient discovery is available here. The capability selects
only approved aliases; the platform supplies sessions and gateway authorization.
Remote App composition supplies RemoteDependencyBindings over a prepared,
gateway-scoped allocator credential. Local compositions may pass
ScopedDependencyBindings directly. The composition owns capability shutdown.
"""
import asyncio
import re

from pantheon.apps.dependency_assembly import _copy, _grant, _identity
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.instance_store import InstanceIntent
from pantheon.factory.instances import AgentInstanceBinding


class DependencyInstanceProvisioner:
    def __init__(self, capability, *, consumer, profiles, tls_context=None, owner=None, rpc_origin=None):
        self._consumer, self._profiles = _copy(consumer), _copy(profiles)
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
                        or profile.keys() - {'alias', 'functions', 'provider'}
                        or not isinstance(profile['alias'], str) or not profile['alias']):
                    raise ValueError('Invalid dependency tool profile')
                if 'provider' in profile:
                    _identity(profile['provider'], provider=True)
                # Validate before allocating anything. No client or connection
                # is created just to validate these caller-visible schemas.
                DependencyToolProvider._validate_functions(profile['functions'])
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', name) or '__' in name:
                    raise ValueError('Invalid dependency tool name')
        self._capability, self._tls_context, self._owner = capability, tls_context, owner

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
                    grant = value['bindings'][profile['alias']]
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
                    tool = DependencyToolProvider(name, client, profile['functions'])
                    created.append(tool)
                    groups[kind][name] = tool
            return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings(**groups))
        except BaseException:
            # Client shutdown drains its own accepted calls, never releases a
            # remote session that older config revisions may still be using.
            await asyncio.gather(*(tool.shutdown() for tool in created))
            raise

    async def retire(self, instance_id):
        value = await self._capability.retire(owner_ref=instance_id)
        if (not isinstance(value, dict) or value.get('consumer') != self._consumer
                or value.get('owner_ref') != instance_id or type(value.get('protocol')) is not int or value['protocol'] != 1
                or value.get('state') not in {'retiring', 'retired'}):
            raise ValueError('Retirement delivery does not match the Agent instance')
        return value
