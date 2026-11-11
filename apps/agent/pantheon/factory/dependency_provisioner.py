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
import time

from pantheonos.appkit.dependency_assembly import _copy, _grant, _identity, CONFIGURATION_BYTES
from pantheonos.appkit.dependency_binding_client import DependencyProviderStarting
from pantheonos.appkit.dependency_client import DependencyCallError, DependencyClient
from pantheonos.appkit.runtime_config import RuntimeCredential
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.instance_store import InstanceIntent
from pantheon.factory.instances import AgentInstanceBinding


# The profile of the Agent's own pantheon_command (general_agent_preset): not
# an App an agent is bound to.
COMMAND_PROFILE = 'pantheon'


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
                functions = DependencyToolProvider._validate_functions(
                    profile['functions'], allow_empty='service_functions' in profile)
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

    @property
    def toolset_names(self):
        """The deployment's Apps an Agent may be bound to."""
        return tuple(name for name in self._profiles['toolsets'] if name != COMMAND_PROFILE)

    def command_grant(self):
        """The Agent App's grant to its own pantheon_command, for the commands
        its agents run (docs/os/pantheon-cli.md); None when the deployment has
        none."""
        if COMMAND_PROFILE not in self._profiles['toolsets']:
            return None
        return self.allocated(COMMAND_PROFILE, owner_ref='agent-commands')

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
        # Each toolset binds on its first call, so a turn starts as soon as
        # the Agent runs: tools whose provider is still starting (the
        # workspace node) fail alone when used, instead of failing the turn.
        # Operation ids derive from the instance intent, so a retried or
        # restarted turn re-reads the same allocation; retire(instance_id)
        # releases all of them.
        prefix = intent.operation_id[:56]
        groups = {kind: {name: AllocatedToolProvider(self, name, intent.instance_id, profile=profile,
                                                     operation=f'{prefix}-{aliases.index(profile["alias"])}')
                         for name, profile in entries.items()}
                  for kind, entries in selected.items()}
        return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings(**groups))

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
        # A call may run as long as an App RPC can (600 s): past the Agent's
        # foreground time it continues in the background rather than failing.
        tool = DependencyToolProvider(name, client, profile['functions'], timeout_seconds=600,
                                      service_functions=profile.get('service_functions'))
        tool.node_id = provider['node_id']  # where this provider instance runs
        return tool

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

    async def _allocate(self, name, owner_ref, attempt, profile=None, operation=None):
        profile = profile or self._profiles['toolsets'][name]
        # An instance's first attempt re-reads its allocation (a restarted
        # Agent, a retried turn); a later one is this process's own, so it
        # never lands on an allocation an earlier process made and lost (its
        # session replaced after a Workspace restart).
        if operation:
            operation_id = f'{operation}-{attempt}' if attempt == 1 else f'{operation}-{self._process}-{attempt}'
        else:
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

# A tool whose App is not running yet waits for it: a paused Workspace resumes
# in about a minute (docs/apps/standalone-apps.md, "The Agent page before the
# Workspace"). It asks the allocator again every PROVIDER_POLL_SECONDS, for up
# to PROVIDER_WAIT_SECONDS; a refused bind allocates nothing. The chat offers
# Start meanwhile and the page starts a paused Workspace by itself
# ("Fixes after combined check 1"), so three minutes cover a cold start twice.
PROVIDER_WAIT_SECONDS = 180.0
PROVIDER_POLL_SECONDS = 3.0
#: How long the probe a run asked for (`prepare`) may take before the tool
#: call that needs it, the provider's own wait included.
PREPARE_SECONDS = PROVIDER_WAIT_SECONDS + 30.0


class ProviderNotStarted(DependencyCallError):
    """The provider's computer did not start within PROVIDER_WAIT_SECONDS:
    nothing ran, and asking again at once would only wait again."""


class AllocatedToolProvider(DependencyToolProvider):
    """A dependency toolset bound on first call and rebound when its grant is lost."""

    def __init__(self, provisioner, name, owner_ref, *, profile=None, operation=None):
        profile = profile or provisioner._profiles['toolsets'][name]
        self._profile, self._operation = profile, operation
        self.toolset_name = name
        self._provisioner, self._owner_ref = provisioner, owner_ref
        self._tools = self._validate_functions(profile['functions'], allow_empty='service_functions' in profile)
        self._services = ({} if 'service_functions' not in profile
                          else self._validate_functions(profile['service_functions']))
        self._pending = set()
        self._closed = False
        self._bound = None
        self._attempt = 0
        self._lock = asyncio.Lock()
        # A probe to run before the next call (prepare), and its lock.
        self._prepare_call = None
        self._prepare_lock = asyncio.Lock()

    async def _current(self):
        async with self._lock:
            self._check_open()
            if self._bound is None:
                try:
                    self._bound = await self._bind()
                except DependencyProviderStarting:
                    # Its App runs on a computer that is starting (a paused
                    # Workspace): wait for it here, shown in the chat, rather
                    # than failing the call for the model to give up on.
                    from pantheon.agent_tools import waiting_for_provider
                    async with waiting_for_provider():
                        self._bound = await self._wait_for_provider()
            return self._bound

    async def _bind(self):
        # Each attempt is a new allocator operation: a lost grant is never
        # revived, the allocator issues a fresh one. The first attempt may
        # re-read an allocation whose session was replaced since (a
        # Workspace restart): the next one is new.
        for tries in (1, 2):
            self._attempt += 1
            try:
                return await self._provisioner._allocate(self.toolset_name, self._owner_ref,
                                                         self._attempt, self._profile, self._operation)
            except DependencyProviderStarting:
                raise
            except DependencyCallError as error:
                if tries == 1 and self._attempt == 1:
                    continue
                # Nothing ran: the provider App is not up. Say so to the model.
                raise DependencyCallError(
                    f'{self.toolset_name} is not available yet; the computer that runs it may still be '
                    'starting. Nothing was executed. Try again shortly, or continue without it.',
                    status=error.status, outcome_unknown=False) from error

    async def _wait_for_provider(self):
        deadline = time.monotonic() + PROVIDER_WAIT_SECONDS
        while True:
            await asyncio.sleep(PROVIDER_POLL_SECONDS)
            self._check_open()
            try:
                return await self._bind()
            except DependencyProviderStarting as error:
                if time.monotonic() >= deadline:
                    raise ProviderNotStarted(
                        f'{self.toolset_name} could not run: your Workspace did not start within '
                        f'{round(PROVIDER_WAIT_SECONDS / 60)} minutes (it may be paused: use Start Workspace in '
                        'the chat or the computer menu, then try again). Nothing was executed.',
                        status=error.status, outcome_unknown=False) from error

    async def ready(self, name, args, *, timeout: float, every: float = 3.0):
        """Call a tool whose call may be repeated safely (a probe such as
        `cd DIR`) until it answers: binding again when the provider ended
        (a Workspace restart), waiting while it starts, up to timeout s. The
        last DependencyCallError when it never answers."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                # A wait for the provider inside the call is bounded by this
                # probe's own timeout.
                return await asyncio.wait_for(self._call(name, args),
                                              max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                self._check_open()
                raise DependencyCallError(f'{self.toolset_name} did not answer within {timeout:.0f} s',
                                          outcome_unknown=False) from None
            except ProviderNotStarted:
                raise
            except DependencyCallError:
                self._check_open()
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(max(0.0, min(every, deadline - time.monotonic())))

    @property
    def is_bound(self) -> bool:
        """Bound to a provider instance (which may have ended since)."""
        return self._bound is not None

    async def bound_node(self):
        """The node the provider instance runs on (binding it if needed)."""
        return getattr(await self._current(), 'node_id', None)

    def prepare(self, name, args):
        """A probe (a call that may be repeated safely, such as `cd DIR`) to run
        with ready() before the next call, instead of before the run: a turn
        starts at once and only a tool that uses this provider waits for it
        (docs/apps/standalone-apps.md, "Fixes after combined check 1"). The
        latest one asked for wins; it stays asked for until it answers."""
        if not isinstance(name, str) or name not in self._tools:
            raise ValueError("Tool is not available in this dependency binding")
        self._prepare_call = (name, dict(args))

    @property
    def preparing(self):
        """The probe the next call runs first, or None."""
        return self._prepare_call

    async def _prepared(self):
        if self._prepare_call is None:
            return
        async with self._prepare_lock:
            pending = self._prepare_call
            if pending is None:
                return
            await self.ready(*pending, timeout=PREPARE_SECONDS)
            if self._prepare_call is pending:
                self._prepare_call = None

    async def call_tool(self, name, args):
        self._check_open()
        if not isinstance(name, str) or name not in self._tools and name not in self._services:
            raise ValueError("Tool is not available in this dependency binding")
        # A probe the run asked for goes first; when it fails nothing else
        # runs and the call reports why (it is asked for again next call).
        await self._prepared()
        return await self._call(name, args)

    async def _call(self, name, args):
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
