"""Agent-instance assembly from an owner-delivered App configuration snapshot.

Config IDs identify reusable recipes, never resource owners. The composition
root binds this callable into AgentEnvironment. Fleet remains unaware of Agent
semantics. This loader does not issue grants, acquire sessions or resolve missing
providers; dynamic provisioning must supply the same instance-bound contract.
"""
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import re
from types import MappingProxyType
from uuid import UUID

from pantheon.apps.runtime_config import RuntimeConfiguration
from pantheon.factory.bindings import AgentToolBindings, _bindings_from_spec, _thaw


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value)


def _config(value):
    """Canonical execution recipe, independent of UI/template file metadata."""
    try:
        value = _thaw(value)
        if (not isinstance(value, dict) or not {'name', 'instructions', 'model', 'icon'} <= value.keys()
                or value.keys() - {'name', 'instructions', 'model', 'icon', 'description', 'toolsets', 'mcp_servers'}
                or any(not isinstance(value[k], str) for k in ('name', 'instructions', 'icon'))
                or not value['name'] or not isinstance(value.get('description', ''), (str, type(None)))):
            raise ValueError
        model = value['model']
        if not ((isinstance(model, str) and model) or (isinstance(model, list) and model
                and all(isinstance(item, str) and item for item in model))):
            raise ValueError
        value.setdefault('description', None)
        for key in ('toolsets', 'mcp_servers'):
            value[key] = value.get(key) or []
            if not isinstance(value[key], list) or not all(_identifier(name) for name in value[key]):
                raise ValueError
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) > 64 * 1024:
            raise ValueError
        return json.loads(encoded), hashlib.sha256(encoded.encode()).hexdigest()
    except (TypeError, ValueError, KeyError, RecursionError):
        raise ValueError('Agent execution configuration is invalid') from None


def config_revision(value):
    """Digest of the complete resolved creation payload, before any await."""
    return _config(value)[1]


@dataclass(frozen=True)
class AgentInstanceBinding:
    instance_id: str
    conversation_id: str
    config_id: str
    config_revision: str
    tools: AgentToolBindings = field(repr=False)

    def __post_init__(self):
        try:
            if (str(UUID(self.instance_id)) != self.instance_id
                    or not _identifier(self.conversation_id) or not _identifier(self.config_id)
                    or not isinstance(self.config_revision, str)
                    or not re.fullmatch(r'[a-f0-9]{64}', self.config_revision)
                    or not isinstance(self.tools, AgentToolBindings)):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Invalid Agent instance binding identity') from None

    def public_identity(self):
        return dict(instance_id=self.instance_id, conversation_id=self.conversation_id,
                    config_id=self.config_id, config_revision=self.config_revision)


class AgentInstanceFactory:
    """One App deployment's instance objects, explicitly assigned to conversations.

    Repeated team creation reuses the same objects; two conversations referencing
    the same config require different instance IDs/bindings. State is recreated
    only by a new factory/deployment, never by silently selecting another config.
    Remote resource survival is decided by the pinned provider, not this cache.
    """

    def __init__(self, instances, *, model_scope=None):
        self._model_scope = model_scope
        self._instances, self._conversations = {}, {}
        transports = set()
        for binding in instances:
            if not isinstance(binding, AgentInstanceBinding) or binding.instance_id in self._instances:
                raise ValueError('Supply unique explicit Agent instances')
            group = self._conversations.setdefault(binding.conversation_id, {})
            if binding.config_id in group:
                raise ValueError('A conversation member needs one unambiguous instance binding')
            for provider in (*binding.tools.toolsets.values(), *binding.tools.mcp_servers.values()):
                if id(provider) in transports:
                    raise ValueError('Each Agent instance must own its client objects, even for shared services')
                transports.add(id(provider))
            self._instances[binding.instance_id] = binding
            group[binding.config_id] = binding
        self._instances = MappingProxyType(self._instances)
        self._agents, self._locks = {}, {}
        self._closed = False

    @classmethod
    def from_runtime_configuration(cls, configuration, *, tls_context=None, model_scope=None):
        """Read values.agent_instances; no legacy config-ID fallback.

        Each tool entry requires owner_ref: the exact instance UUID for an owned
        resource, or null for explicitly shared access. This declaration is not
        authorization; the gateway must enforce the actual grant-bound session.
        Credential reuse across owned instances is rejected, including aliases.
        """
        try:
            if not isinstance(configuration, RuntimeConfiguration):
                raise ValueError
            root = _thaw(configuration.values['agent_instances'])
            if (not isinstance(root, dict) or set(root) != {'protocol', 'instances'}
                    or type(root['protocol']) is not int or root['protocol'] != 1
                    or not isinstance(root['instances'], dict) or len(root['instances']) > 256):
                raise ValueError
            instances, credentials = [], {}
            for identity, entry in root['instances'].items():
                if (not isinstance(entry, dict) or set(entry) != {
                        'conversation_id', 'config_id', 'config_revision', 'toolsets', 'mcp_servers'}):
                    raise ValueError
                spec = {group: entry[group] for group in ('toolsets', 'mcp_servers')}
                for group in spec.values():
                    if not isinstance(group, dict):
                        raise ValueError
                    for tool in group.values():
                        ref = tool.pop('owner_ref')
                        if ref is not None and ref != identity:
                            raise ValueError
                        credential = configuration.credentials[tool['credential']]
                        fingerprint = (credential.endpoint, credential.key)
                        if fingerprint in credentials:
                            previous = credentials[fingerprint]
                            if previous != ref:
                                raise ValueError
                        credentials[fingerprint] = ref
                tools = _bindings_from_spec(configuration, spec, tls_context)
                instances.append(AgentInstanceBinding(identity, entry['conversation_id'],
                    entry['config_id'], entry['config_revision'], tools))
            return cls(instances, model_scope=model_scope)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise ValueError('Agent instance configuration is invalid or incomplete') from None

    async def __call__(self, agent_configs, *, conversation_id=None):
        from pantheon.factory import create_agent
        if self._closed:
            raise RuntimeError('Agent instance factory is stopping')
        if not _identifier(conversation_id) or conversation_id not in self._conversations:
            raise ValueError('No explicit Agent instances are bound to this conversation')
        group = self._conversations[conversation_id]
        if not isinstance(agent_configs, dict) or set(agent_configs) != set(group):
            raise ValueError('Supply exactly the configured conversation members')
        # Snapshot/validate every input before creating anything or awaiting a
        # lock. A config edit cannot redirect an already-bound instance.
        configs = {}
        for identity, value in agent_configs.items():
            config, revision = _config(value)
            if revision != group[identity].config_revision:
                raise ValueError('Agent config revision changed; provision updated instance bindings')
            configs[identity] = config
        # Team currently routes members by display name. Reject collisions
        # rather than silently dropping a member with its live resources.
        if len({value['name'] for value in configs.values()}) != len(configs):
            raise ValueError('Conversation member names must be distinct')
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            if self._closed:
                raise RuntimeError('Agent instance factory is stopping')
            result = []
            for key, config in configs.items():
                binding = group[key]
                agent = self._agents.get(binding.instance_id)
                if agent is None:
                    agent = await create_agent(**config, tool_bindings=binding.tools,
                                               instance_id=UUID(binding.instance_id),
                                               model_scope=self._model_scope)
                    agent._instance_identity = binding.public_identity()
                    self._agents[binding.instance_id] = agent
                result.append(agent)
            return result

    def bindings_for(self, agent):
        """Return capabilities only for an object assembled by this factory.

        Model-authored names/IDs are not proof of ownership. Plugins borrow the
        exact instance clients and the composition closes them after all work.
        """
        if self._closed:
            raise RuntimeError('Agent instance factory is stopping')
        for identity, candidate in self._agents.items():
            if candidate is agent:
                return self._instances[identity].tools
        raise ValueError('Agent does not belong to this instance factory')

    async def shutdown(self):
        self._closed = True
        # Wait for accepted assembly before closing even unused/partially built
        # clients. Remote session/grant release is owned by the control plane.
        for lock in tuple(self._locks.values()):
            async with lock:
                pass
        providers = [provider for binding in self._instances.values()
                     for provider in (*binding.tools.toolsets.values(), *binding.tools.mcp_servers.values())]
        results = await asyncio.gather(*(provider.shutdown() for provider in providers), return_exceptions=True)
        failures = [error for error in results if isinstance(error, BaseException)]
        if failures:
            raise RuntimeError('Agent instance clients did not finish shutdown') from failures[0]
