"""Explicit tool bindings supplied by the Agent App composition root.

These are not credentials or session factories exposed to model-authored config.
The owner must acquire each Agent instance's sessions/grants before assembly.
Shared stateless providers can be reused inside one deployment; a Shell binding
must refer to that Agent instance's session. No global resolver fills gaps.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import ssl
from types import MappingProxyType

from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.runtime_config import RuntimeConfiguration
from pantheon.dependency_provider import DependencyToolProvider


@dataclass(frozen=True)
class AgentToolBindings:
    toolsets: Mapping[str, DependencyToolProvider] = field(default_factory=dict, repr=False)
    mcp_servers: Mapping[str, DependencyToolProvider] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        for attribute in ("toolsets", "mcp_servers"):
            providers = dict(getattr(self, attribute))
            for name, provider in providers.items():
                if not isinstance(provider, DependencyToolProvider) or name != provider.toolset_name:
                    raise ValueError("Explicit Agent bindings require named dependency tool providers")
            object.__setattr__(self, attribute, MappingProxyType(providers))

    async def attach(self, agent, toolsets: list[str], mcp_servers: list[str]):
        # 'think' and 'task' are execution-engine/plugin capabilities, not
        # remotely discovered services. Implicit global MCP settings do not
        # participate in explicit assembly.
        wanted, mcps = [], list(mcp_servers)
        for spec in toolsets:
            if spec in ("think", "task"):
                continue
            if spec == "mcp":
                mcps.append("mcp")
            elif spec.startswith("mcp:"):
                mcps.append(spec[4:])
            else:
                wanted.append(spec)
        selected = {}
        for names, providers in ((wanted, self.toolsets), (mcps, self.mcp_servers)):
            for name in dict.fromkeys(names):
                if name not in providers:
                    raise ValueError(f"Missing explicit Agent dependency: {name}")
                if name in selected and selected[name] is not providers[name]:
                    raise ValueError("Conflicting explicit Agent dependency names")
                selected[name] = providers[name]
        for provider in selected.values():
            await provider.initialize()
        for provider in selected.values():
            await agent.toolset(provider)
        agent._explicit_tool_bindings = True
        # Deployment cleanup closes these only after runs, background tools and
        # plugin shutdown have drained. Legacy singleton providers stay separate.
        agent._owned_tool_providers = tuple(selected.values())


def bindings_from_runtime_configuration(configuration: RuntimeConfiguration, *,
                                        tls_context: ssl.SSLContext | None = None) -> Mapping[str, AgentToolBindings]:
    """Build bindings from the Fleet-delivered snapshot, never Agent templates.

    values.agent_tools = {protocol: 1, agents: {config_id: {toolsets: {
        shell: {credential: 'shell_a', functions: [...], timeout_seconds: 60}
    }, mcp_servers: {...}}}}

    Each credential name resolves only in this component's credential snapshot.
    Session creation, grant renewal and authoritative schema projection belong
    to the composition/control layer; this consumer does not mint permissions.
    ``tls_context`` is an explicit private-CA integration, not an env override.
    """
    def thaw(value):
        if isinstance(value, Mapping):
            return {key: thaw(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [thaw(item) for item in value]
        return value

    try:
        if not isinstance(configuration, RuntimeConfiguration):
            raise ValueError
        root = thaw(configuration.values["agent_tools"])
        if (set(root) != {"protocol", "agents"} or type(root["protocol"]) is not int
                or root["protocol"] != 1 or not isinstance(root["agents"], dict)):
            raise ValueError
        bindings = {}
        for identity, spec in root["agents"].items():
            if (not isinstance(identity, str) or not identity
                    or not isinstance(spec, dict) or not set(spec) <= {"toolsets", "mcp_servers"}):
                raise ValueError
            groups = {}
            for kind in ("toolsets", "mcp_servers"):
                entries = spec.get(kind, {})
                if not isinstance(entries, dict):
                    raise ValueError
                providers = {}
                for name, entry in entries.items():
                    if (not isinstance(entry, dict)
                            or not {"credential", "functions"} <= set(entry)
                            or not set(entry) <= {"credential", "functions", "timeout_seconds", "max_inflight"}):
                        raise ValueError
                    client = DependencyClient(configuration.credentials[entry["credential"]], tls_context=tls_context)
                    providers[name] = DependencyToolProvider(name, client, entry["functions"],
                        timeout_seconds=entry.get("timeout_seconds", 60), max_inflight=entry.get("max_inflight", 8))
                groups[kind] = providers
            bindings[identity] = AgentToolBindings(**groups)
        return MappingProxyType(bindings)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise ValueError("Agent dependency configuration is invalid or incomplete") from None
