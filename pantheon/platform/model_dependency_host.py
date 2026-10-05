"""Prepared owner-side Model Services facade on the ordinary App HTTP host.

This headless service holds the Hub credential; consumers receive only a
model_services_control grant with policy_id bound by the dependency gateway.
"""
import asyncio
from collections.abc import Mapping
import ssl

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.models.dependency_service import ModelServiceControl
from pantheon.platform.model_dependency_control import ModelDependencyControl


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


class ModelDependencyHost:
    def __init__(self, *, configuration, tls_context=None, transport=None):
        try:
            spec = _plain(configuration.values['model_services'])
            if (configuration.component != 'backend' or not configuration.owner
                    or set(configuration.values) != {'model_services'}
                    or set(configuration.credentials) != {'hub'}
                    or not {'protocol', 'policies'} <= set(spec)
                    or set(spec) - {'protocol', 'policies', 'trust_roots_pem', 'http_origin'}
                    or type(spec['protocol']) is not int or spec['protocol'] != 1):
                raise ValueError
            if 'trust_roots_pem' in spec:
                pem = spec['trust_roots_pem']
                if not isinstance(pem, str) or not pem or len(pem) > 16384 or tls_context is not None:
                    raise ValueError
                tls_context = ssl.create_default_context(cadata=pem)
            # Validate immutable policies before allocating an HTTP client.
            self.service = ModelServiceControl(None, policies=spec['policies'])
            self.client = ModelDependencyControl(owner=configuration.owner,
                credential=configuration.credentials['hub'], tls_context=tls_context, transport=transport,
                http_origin=spec.get('http_origin'))
            self.service.client = self.client
            self.service.issue_connection = self.client.issue_connection
        except (KeyError, ValueError, TypeError, AttributeError, ssl.SSLError):
            raise AssemblyError('Invalid Model Services owner configuration') from None
        self._accepting = True
        self._active = set()

    async def model_services_control(self, *, policy_id, operation, arguments):
        if not self._accepting:
            raise AssemblyError('Model Services owner is stopping')
        task = asyncio.current_task()
        self._active.add(task)
        try:
            return await self.service.model_services_control(policy_id=policy_id,
                                                             operation=operation, arguments=arguments)
        finally:
            self._active.discard(task)

    async def close(self):
        self._accepting = False
        if self._active:
            await asyncio.gather(*tuple(self._active), return_exceptions=True)
        await self.client.aclose()


async def register(ctx):
    ctx.require_rpc_token = True
    host = ModelDependencyHost(configuration=load_runtime_configuration(required=True))
    ctx.method(host.model_services_control)
    ctx.concurrent_methods.add('model_services_control')
    ctx.on_cleanup(host.close)
