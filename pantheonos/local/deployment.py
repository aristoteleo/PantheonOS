"""The General Team on a single-machine Fleet, deployed by its controller.

The owner's setup is composed (``compose_profile``) into the same deployment
as on a cloud Fleet (docs/os/fleet-orchestration.md): the controller's reconciler
installs, starts, restarts and stops the Apps. This module only renders the
composition's single-machine values, stores the owner's secrets with the
controller, saves the deployment and reports its status. It never starts or
stops an App itself.

Single-machine values (``{"$local": ...}`` in the composition):

- ``controller``, ``trust_roots_pem``, ``workspace``, ``directory_root``:
  this profile's literal values (one node, the same machine).
- ``owner_credential`` / ``fleet_credential``: the owner key, delivered as the
  ``owner-controller`` secret; App buses use it as on a cloud Fleet
  (``fleet-key``), trusting this profile's private CA.
- ``fleet_event_prefix``: the controller's ``{"$fleet": "event_prefix"}``.

Credentials from the owner's setup (``node-secret://<name>``) become
deployment secrets of the same name. The Connector registers itself in the
profile's model directory (``directory.DirectoryServer``).
"""
import asyncio
import hashlib
import io
from pathlib import Path
import tarfile

import httpx

from pantheonos.appkit.dependency_assembly import AssemblyError
from pantheonos.general_team.release_profile import LOCAL_HOSTED, _app, hosted_apps, model_app
from pantheonos.appkit.release_set import INDEX

NAME = 'general-team'
OWNER_SECRET = 'owner-controller'
DIRECTORY_SECRET = 'directory'
SECRET_PREFIX = 'node-secret://'
LITERAL = ('controller', 'trust_roots_pem', 'workspace', 'directory_root')


def render(spec, context):
    """Deployment Apps for a composed local profile, and the secrets they name.

    context: controller, nats, trust_roots_pem, workspace, directory_root.
    Returns (apps, secrets) where secrets maps each secret name to the
    endpoint it is bound to (None when the owner's credential file says).
    """
    secrets = {}

    def value(v, app, *, key=None):
        if isinstance(v, dict):
            if '$local' in v:
                name = v['$local']
                if set(v) != {'$local'}:
                    raise AssemblyError(f'{app}: invalid local value')
                if name in LITERAL:
                    return context[name]
                if name in ('owner_credential', 'fleet_credential'):
                    secrets[OWNER_SECRET] = context['controller']
                    return {'$secret': OWNER_SECRET}
                if name == 'fleet_event_prefix':
                    return {'$fleet': 'event_prefix'}
                raise AssemblyError(f'{app}: unknown local value {name!r}')
            if v == {'auth': 'creds-base64'}:
                return {'auth': 'fleet-key', 'url': context['nats'],
                        'controller_ca_pem': context['trust_roots_pem']}
            if set(v) == {'$model'}:
                return 'current'  # the Connector registers its running instance itself
            if set(v) == {'ref', 'endpoint'} and isinstance(v['ref'], str) and v['ref'].startswith(SECRET_PREFIX):
                name = _secret_name(v['ref'])
                secrets[name] = v['endpoint']
                return {'$secret': name}
            return {k: value(item, app, key=k) for k, item in v.items()}
        if isinstance(v, list):
            return [value(item, app) for item in v]
        if key == 'secret_ref' and isinstance(v, str) and v.startswith(SECRET_PREFIX):
            name = _secret_name(v)
            secrets.setdefault(name, None)
            return {'$secret_ref': name}
        return v

    apps = hosted_apps({name: _app(name, app, value) for name, app in spec['apps'].items()}, LOCAL_HOSTED)
    for name, item in spec['model_apps'].items():
        apps[name] = model_app(name, item, render=value)
        secrets[DIRECTORY_SECRET] = None  # this launcher's directory endpoint
    return apps, secrets


def _secret_name(ref):
    name = ref[len(SECRET_PREFIX):]
    if not name or len(name) > 64 or not name.replace('-', '').replace('_', '').isalnum() or not name[0].isalpha():
        raise AssemblyError('Invalid credential reference')
    return name


def release_archive(release, releases):
    """A release-set archive of the product's Apps, inside the controller's
    release directory. Returns (file URL, SHA-256)."""
    release, releases = Path(release), Path(releases)
    index = (release / INDEX).read_bytes()
    target = releases / ('release-' + hashlib.sha256(index).hexdigest()[:32] + '.tar.gz')
    if not target.exists():
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            archive.add(release / INDEX, arcname=INDEX)
            artifacts = sorted((release / 'artifacts').iterdir())
            for artifact in artifacts:
                archive.add(artifact, arcname='artifacts/' + artifact.name)
        _write_private(target, buffer.getvalue())
    return target.as_uri(), hashlib.sha256(target.read_bytes()).hexdigest()


def _write_private(path, data):
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    temporary.replace(path)


class LocalDeployment:
    """The owner's side of one single-machine deployment (status, stop, RPC)."""

    def __init__(self, runtime, *, owner_key, tls_context):
        self.runtime, self.info = runtime, runtime.coordinates
        self._key = owner_key
        self._http = httpx.AsyncClient(base_url=self.info.controller, verify=tls_context, trust_env=False,
                                       timeout=30, headers={'Authorization': 'Bearer ' + owner_key})
        self.current = None

    async def aclose(self):
        await self._http.aclose()

    async def _call(self, method, path, body=None):
        response = await self._http.request(method, path, json=body)
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail', '')
            except ValueError:
                detail = ''
            raise AssemblyError(f'Local Fleet controller refused {method} {path.split("?")[0]}: {detail}'.strip())
        return response.json() if response.content else None

    async def put_secret(self, name, value, endpoint):
        await self._call('PUT', '/secrets/' + name, {'value': value, 'endpoint': endpoint})

    async def get(self):
        response = await self._http.get('/deployments/' + NAME)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        self.current = response.json()
        return self.current

    async def apply(self, spec):
        """Save the desired deployment (the reconciler converges to it)."""
        current = await self.get()
        self.current = await self._call('PUT', '/deployments/' + NAME,
                                        {'revision': current['revision'] if current else 0, 'spec': spec})
        return self.current

    async def set_intent(self, intent):
        current = await self.get()
        if current is None:
            return None
        spec = current['spec']
        if all(app['intent'] == intent for app in spec['apps'].values()):
            return current
        for app in spec['apps'].values():
            app['intent'] = intent
        return await self.apply(spec)

    def summary(self):
        """{state, apps} for the native owner: starting, ready, stopping, stopped."""
        current = self.current or {}
        spec = current.get('spec') or {}
        apps = (current.get('status') or {}).get('apps') or {}
        stopping = any(app.get('intent') == 'stopped' for app in (spec.get('apps') or {}).values())
        states = {name: (apps.get(name) or {}).get('state', 'waiting') for name in spec.get('apps', {})}
        if stopping:
            state = 'stopped' if states and all(s == 'stopped' for s in states.values()) else 'stopping'
        else:
            state = 'ready' if states and all(s == 'ready' for s in states.values()) else 'starting'
        attention = sorted(name for name, s in states.items() if s in ('attention', 'failed', 'backoff'))
        value = {'state': state, 'profile': str(self.runtime.root),
                 'apps': {name: {'state': s, **({'reason': apps[name]['reason']} if apps.get(name, {}).get('reason') else {})}
                          for name, s in states.items()}}
        if attention:
            value['attention'] = attention
        return value

    async def wait(self, state, *, report=None, interval=1.0, stop=None):
        """Poll status until the summary reaches state (or stop is set)."""
        previous = None
        while True:
            await self.get()
            summary = self.summary()
            if report is not None and summary != previous:
                await report(summary)
            previous = summary
            if summary['state'] == state:
                return summary
            if stop is not None and stop():
                return summary
            await asyncio.sleep(interval)

    def app_binding(self, alias):
        """The App's current instance, as the controller observed it ready."""
        status = ((self.current or {}).get('status') or {}).get('apps', {}).get(alias) or {}
        if status.get('state') != 'ready':
            raise AssemblyError(f'{alias} is not ready')
        return {key: status[key] for key in ('node_id', 'instance_id', 'revision', 'generation')}

    async def bind_rpc(self, resolver, alias, app_id):
        """An owner RPC pinned to the App's current generation (via the owner bus)."""
        binding = self.app_binding(alias)
        node_id = binding.pop('node_id')
        client = await resolver._ensure_client()

        async def invoke(method, arguments, timeout=30):
            response = await client.invoke(node_id, app_id, binding, method, arguments, timeout)
            if ('error' in response or not isinstance(response.get('response'), dict)
                    or response['response'].get('success') is not True or 'result' not in response['response']):
                raise AssemblyError('App RPC failed or its outcome is unknown; inspect this exact App generation')
            return response['response']['result']
        return invoke


def deployment_spec(apps, release_url, sha256, secrets):
    return {'release': {'url': release_url, 'sha256': sha256}, 'apps': apps, 'secrets': sorted(secrets)}
