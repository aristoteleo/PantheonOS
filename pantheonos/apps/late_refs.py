"""Late App references: an App's configuration names another App of its Fleet
deployment ({"$late_app": name, "deployment": d, ...}) and resolves the
current instance only when it needs it, from the deployment's status at the
owner's Fleet controller (docs/fleet-orchestration.md). Such references are
not start dependencies, so an App holding them starts independently.
"""
import asyncio
import re
import time

_NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')


def has_late(value):
    if isinstance(value, dict):
        return '$late_app' in value or any(has_late(v) for v in value.values())
    if isinstance(value, list):
        return any(has_late(v) for v in value)
    return False


class DeploymentInstances:
    """Current App instances from the owner's deployment status (cached briefly)."""

    def __init__(self, credential, *, tls_context=None, ttl=5.0, transport=None):
        import httpx
        self._http = httpx.AsyncClient(base_url=credential.endpoint.rstrip('/'), verify=tls_context or True,
                                       trust_env=False, follow_redirects=False, timeout=10, transport=transport)
        self._key, self._ttl = credential.key, ttl
        self._cache = {}
        self._lock = asyncio.Lock()

    async def status(self, deployment):
        if not isinstance(deployment, str) or not _NAME.fullmatch(deployment):
            raise ValueError('Invalid deployment reference')
        async with self._lock:
            cached = self._cache.get(deployment)
            if cached and time.monotonic() - cached[0] < self._ttl:
                return cached[1]
            response = await self._http.get('/deployments/' + deployment,
                                             headers={'Authorization': 'Bearer ' + self._key})
            response.raise_for_status()
            apps = (response.json().get('status') or {}).get('apps') or {}
            self._cache[deployment] = (time.monotonic(), apps)
            return apps

    async def instance(self, deployment, app):
        """The identity an App runs (or is starting) at; refreshed once if absent."""
        for attempt in range(2):
            status = (await self.status(deployment)).get(app) or {}
            if (status.get('state') in ('ready', 'starting') and status.get('node_id')
                    and status.get('instance_id') and status.get('revision') and status.get('generation')):
                return {'node_id': status['node_id'], 'instance_id': status['instance_id'],
                        'revision': status['revision'], 'generation': int(status['generation'])}
            self._cache.pop(deployment, None)
        raise ValueError(f'{app} is not running yet')

    async def resolve(self, value):
        """Replace every late reference in value with the App's current instance."""
        if isinstance(value, dict):
            if '$late_app' in value:
                extra = {k: v for k, v in value.items() if k not in ('$late_app', 'deployment')}
                return {**await self.instance(value['deployment'], value['$late_app']), **extra}
            return {k: await self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [await self.resolve(v) for v in value]
        return value

    async def aclose(self):
        await self._http.aclose()
