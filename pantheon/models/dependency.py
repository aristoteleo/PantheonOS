"""Model Services consumer over an ordinary, owner-issued App dependency.

Only small control messages use RPC. Inference, artifacts and cancellation use
ModelServices' existing HTTP/direct data plane. The control provider must bind
the consumer policy in its gateway grant and issue consumer-scoped data grants;
this client never acquires a Fleet owner key or chooses its own policy.
"""
import asyncio
import re

from pantheon.apps.dependency_client import DependencyClient, DependencyCallError
from pantheon.apps.dependency_binding_client import _drain
from .client import ModelServices, ControlError


def control_operation(method, path, data):
    """Translate existing model control calls into a closed dependency API."""
    if method == 'GET' and data is None:
        if path == '/api/model-services':
            return 'deployments', {}
        if path == '/api/model-services/routes':
            return 'routes', {}
    if method == 'POST' and isinstance(data, dict):
        if path == '/api/fleet/apps/workload-connect':
            return 'connect', {'binding': data}
        if path == '/api/fleet/apps/workload-direct-connect':
            return 'direct_connect', {'binding': {k: v for k, v in data.items() if k != 'peer_id'},
                                      'peer_id': data.get('peer_id')}
        match = re.fullmatch(r'/api/model-services/routes/([a-z0-9][a-z0-9_-]{0,63})/resolve', path)
        if match:
            return 'resolve', {'route_id': match[1], 'requirements': data}
        match = re.fullmatch(r'/api/model-services/([a-z0-9][a-z0-9_-]{0,63})/engine-idle', path)
        if match and set(data) == {'action', 'revision'} and data['action'] in ('status', 'wake'):
            return 'engine_idle', {'deployment_id': match[1], **data}
    raise ControlError(403, 'This model dependency grants inference, not model management')


class DependencyModelServices(ModelServices):
    """No ambient discovery, token fallback, control retries or secret errors."""

    def __init__(self, client: DependencyClient, *, transport=None, direct_executable=None):
        if not isinstance(client, DependencyClient):
            raise ValueError('Supply an explicit Model Services dependency')
        # Nonempty sentinel prevents the base class from discovering a Hub URL.
        # hub_request and headers below never use that URL or an owner token.
        super().__init__(hub='dependency://model-services', token=None, transport=transport,
                         direct_executable=direct_executable, prefetch_direct_grants=False)
        self._dependency = client
        self._pending = set()
        self._slots = asyncio.Semaphore(8)
        self._closed = False

    def headers(self):
        raise RuntimeError('Model dependency credentials cannot authorize Hub requests')

    async def hub_request(self, method, path, data=None):
        operation, arguments = control_operation(method, path, data)
        async with self._slots:
            if self._closed:
                raise RuntimeError('Model dependency is stopping')
            task = asyncio.create_task(asyncio.to_thread(self._dependency.invoke,
                'model_services_control', {'operation': operation, 'arguments': arguments},
                timeout_seconds=60))
            self._pending.add(task)
            try:
                response = await _drain(task)
                if (not isinstance(response, dict) or response.get('success') is not True
                        or not isinstance(response.get('result'), dict)):
                    raise ControlError(502, 'Model dependency returned an invalid response')
                result = response['result']
                if (set(result) != {'protocol', 'operation', 'status', 'result'}
                        or type(result['protocol']) is not int or result['protocol'] != 1
                        or result['operation'] != operation or type(result['status']) is not int
                        or result['status'] != 200 and not 400 <= result['status'] <= 599):
                    raise ControlError(502, 'Model dependency returned an invalid response')
                if result['status'] != 200:
                    # Never expose an upstream response body/URL/credential.
                    raise ControlError(result['status'])
                if not isinstance(result['result'], dict):
                    raise ControlError(502, 'Model dependency returned an invalid response')
                return result['result']
            except DependencyCallError as error:
                raise ControlError(error.status or 503, 'Model dependency is unavailable or no longer authorized') from None
            finally:
                self._pending.discard(task)

    async def aclose(self):
        self._closed = True
        async def finish():
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
            await super(DependencyModelServices, self).aclose()
            self.grants.clear()
            self.metadata.clear()
        await _drain(asyncio.create_task(finish()))
