"""Explicit configuration/capabilities for one App's model calls.

This object does not grant access or acquire Fleet credentials. The composition
supplies authorized clients and OAuth managers. Absence never means consulting
the process's Fleet client or importing another OS user's CLI login.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(eq=False)
class ModelCallScope:
    settings: Any = field(repr=False)
    fleet_client: Any = field(default=None, repr=False)
    oauth_managers: dict = field(default_factory=dict, repr=False)
    resolve_models: Callable | None = field(default=None, repr=False)
    responses_unavailable: set = field(default_factory=set, repr=False)

    def fleet(self):
        if self.fleet_client is None:
            raise RuntimeError('This Agent App has no bound Model Services client')
        return self.fleet_client

    def oauth(self, provider):
        manager = self.oauth_managers.get(provider)
        if manager is None:
            raise RuntimeError(f'This Agent App has no bound {provider} OAuth session')
        return manager

    def models(self, spec):
        if self.resolve_models is None:
            raise RuntimeError('This Agent App has no bound model selector')
        result = self.resolve_models(spec)
        if not isinstance(result, list) or not result or not all(isinstance(m, str) and m for m in result):
            raise ValueError('The bound model selector returned no valid model chain')
        return list(result)
