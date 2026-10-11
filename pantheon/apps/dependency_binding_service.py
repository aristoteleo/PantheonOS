"""Generic owner-side RPC facade for live dependency allocation.

Expose allocation and retirement through an authenticated ordinary App host.
The dependency gateway must bind ``policy_id`` as a non-caller argument, and
pin the consumer and this provider generation. Policies are an immutable owner
configuration, never App-supplied RPC arguments. This facade is not itself an
authentication layer and must not be exposed directly to an untrusted network.
"""
import logging
import re

from pantheon.apps.dependency_assembly import AssemblyError, NAME, _copy, _matches
from pantheon.apps.live_dependencies import ScopedDependencyBindings

# The allocator package vendors only a minimal runtime; stdlib logging reaches its log.
logger = logging.getLogger(__name__)


def _log_hidden(what, exc):
    """Keep the cause in this owner's private App log; callers get a generic error."""
    text = re.sub(r'pbk_[A-Za-z0-9_-]+', 'pbk_<redacted>', f'{type(exc).__name__}: {exc}')
    text = re.sub(r'//[^/@\s]+@', '//<redacted>@', text)
    logger.warning(f'[dependency-binding] {what} failed: {text[:1000]}')


class DependencyBindingService:
    """Serve fixed policies without handing consumers Fleet management access.

    One instance belongs to one platform owner. The composition supplies its
    LiveDependencyOwner (including maintenance and durable journals) and exact
    policies before exposing the service. Updating policy requires a new host
    configuration/generation and a new gateway grant; no mutable policy RPC is
    exposed. Accepted operations use the owner's existing idempotency journals.
    """

    def __init__(self, owner, *, policies, instances=None):
        policies = _copy(policies)
        if (not isinstance(policies, dict) or not 1 <= len(policies) <= 64
                or not all(_matches(NAME, name) for name in policies)):
            raise AssemblyError('Supply explicit dependency allocation policies')
        self._policies = {}
        for name, policy in policies.items():
            if not isinstance(policy, dict) or set(policy) != {'consumer', 'bindings'}:
                raise AssemblyError('Invalid dependency allocation policy')
            self._policies[name] = ScopedDependencyBindings(owner, **policy, instances=instances)

    async def bind_dependencies(self, *, policy_id, owner_ref, operation_id, aliases):
        """Allocate approved aliases; policy_id is injected by the gateway."""
        try:
            if not isinstance(policy_id, str) or policy_id not in self._policies:
                raise AssemblyError('Unknown dependency allocation policy')
            return await self._policies[policy_id].bind(
                owner_ref=owner_ref, operation_id=operation_id, aliases=aliases)
        except Exception as exc:
            # An upstream exception may contain management credentials/URLs.
            # It can follow a committed allocation: the client must retry only
            # the same durable operation, never fabricate a replacement ID.
            _log_hidden('allocation', exc)
            raise AssemblyError('Dependency allocation unavailable; retry the original operation or inspect its owner') from None

    async def retire_dependencies(self, *, policy_id, owner_ref):
        """Retire only a logical owner within the gateway-bound consumer."""
        try:
            if not isinstance(policy_id, str) or policy_id not in self._policies:
                raise AssemblyError('Unknown dependency allocation policy')
            return await self._policies[policy_id].retire(owner_ref=owner_ref)
        except Exception as exc:
            _log_hidden('retirement', exc)
            raise AssemblyError('Dependency retirement incomplete; retry the same logical owner') from None
