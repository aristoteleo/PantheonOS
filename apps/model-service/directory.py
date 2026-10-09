"""Register this Connector in its owner's Hub model directory.

Configured by the deployment (docs/fleet-orchestration.md §9): the
``directory`` value names the deployment id, display name, the models to
publish and the tier routes to seed; the ``directory`` credential is a Hub key
for the owner. The binding is this process's own Fleet identity, so the row
always names the running instance. The owner's later choices are kept: a
model they withdrew is not published again (offered models are remembered in
the Connector's data, which moves with it), and an existing route keeps its
models. The credential is bound to the Hub's model directory API
(``https://<hub>/api/model-services``). Standard library only; bundled with
the Connector package.
"""
import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from server import NoRedirect

FIELDS = {'deployment_id', 'name', 'models', 'routes'}
MODEL_FIELDS = {'id', 'required', 'context_limit', 'suggested'}


def validate(value):
    """The ``directory`` configuration value, normalized."""
    if not hasattr(value, 'items') or set(value) - FIELDS or not {'deployment_id', 'name', 'models'} <= set(value):
        raise ValueError('directory needs deployment_id, name and models')
    value = json.loads(json.dumps(value, default=dict))
    if not isinstance(value['deployment_id'], str) or not isinstance(value['name'], str) or not value['name']:
        raise ValueError('directory needs a deployment id and name')
    models = value['models']
    if not isinstance(models, list) or len(models) > 1000:
        raise ValueError('directory models is a list of at most 1000 models')
    for m in models:
        if not isinstance(m, dict) or set(m) - MODEL_FIELDS or not isinstance(m.get('id'), str):
            raise ValueError('directory models are {id, required, context_limit, suggested}')
    routes = value.get('routes') or {}
    if not isinstance(routes, dict) or any(not isinstance(c, list) for c in routes.values()):
        raise ValueError('directory routes map a route name to its ordered models')
    value['routes'] = routes
    return value


def chat_entry(model_id, reported, suggested=None, context_limit=None):
    """Mirror of pantheon.models.manager.ModelServiceManager.chat_entry (API engine)."""
    reported, suggested = reported or {}, suggested or {}
    entry = {'id': model_id, 'name': model_id, 'operations': ['text'], 'compute': 'provider'}
    for key in ('tools', 'vision', 'reasoning'):
        if isinstance(reported.get(key), bool):
            entry[key] = reported[key]
        elif isinstance(suggested.get(key), bool):
            entry[key] = suggested[key]
    if reported.get('operations') in (['text'], ['embedding']):
        entry['operations'] = reported['operations']
    stated = reported.get('context') or suggested.get('context')
    if context_limit is not None:
        entry['context_limit'] = context_limit
        entry['context'] = min(context_limit, stated) if stated else context_limit
    elif stated:
        entry['context'] = stated
    if 'text' in entry['operations'] and not entry.get('context'):
        raise ValueError('no stated context')
    return entry


class Hub:
    def __init__(self, credential):
        self.base, self.key = credential.endpoint.rstrip('/'), credential.key
        if not self.base.endswith('/api/model-services'):
            raise ValueError('the directory credential is bound to https://<hub>/api/model-services')
        self.opener = build_opener(NoRedirect)

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=data, method=method, headers={
            'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=20) as response:
                return json.loads(response.read(4 * 1024 * 1024) or b'null')
        except HTTPError as error:
            detail = ''
            try:
                detail = json.loads(error.read(65536)).get('detail', '')
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(f'Hub {method} {path.split("?")[0]}: HTTP {error.code} {detail}'.strip()) from None


class Registration:
    def __init__(self, connector, configuration, value, credential, *, log=print):
        self.connector, self.value, self.hub = connector, validate(value), Hub(credential)
        self.binding = {'node_id': configuration.node_id, 'instance_id': configuration.instance_id,
                        'revision': configuration.revision, 'generation': configuration.generation + 0,
                        'component': 'backend', 'port': 'http'}
        self.offered_path = Path(connector.data) / 'directory-offered.json'
        self.log = log

    def offered(self):
        try:
            return set(json.loads(self.offered_path.read_text()))
        except (OSError, ValueError):
            return set()

    def remember(self, models):
        tmp = self.offered_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(sorted(models)))
        tmp.replace(self.offered_path)

    def models(self, existing):
        """What to publish: kept owner entries, plus configured models not offered before."""
        discovered = {m['id']: m.get('reported') or {} for m in self.connector.discover()['models']}
        offered = self.offered()
        current = {m['id']: m for m in (existing or {}).get('models') or []}
        published, now_offered = [], set(offered)
        for model_id, entry in current.items():
            if model_id in discovered:
                published.append(entry)  # the owner's entry, limits included
        for m in self.value['models']:
            model_id = m['id']
            if model_id in current or model_id not in discovered:
                if m.get('required') and model_id not in discovered:
                    raise RuntimeError(f'the service does not offer required model {model_id}')
                continue
            if model_id in offered and not m.get('required'):
                continue  # offered before and withdrawn by the owner
            try:
                entry = chat_entry(model_id, discovered[model_id], m.get('suggested'), m.get('context_limit'))
            except ValueError:
                if m.get('required'):
                    raise RuntimeError(f'required model {model_id} states no context; set a context limit')
                continue
            if not m.get('required') and (entry.get('tools') is not True or entry['operations'] != ['text']):
                continue  # the catalog offers tool-capable text models (an Agent needs tools)
            published.append(entry)
            now_offered.add(model_id)
        return published, now_offered

    def sync(self):
        deployment_id = self.value['deployment_id']
        status = self.connector.activity_status() if hasattr(self.connector, 'activity_status') else {}
        if status.get('accepting') is False:
            raise RuntimeError('the connector is not accepting requests yet')
        rows = self.hub.request('GET', '')['deployments']
        existing = next((r for r in rows if r['deployment_id'] == deployment_id), None)
        models, offered = self.models(existing)
        row = {'deployment_id': deployment_id, 'name': (existing or {}).get('name') or self.value['name'],
               'node_id': self.binding['node_id'], 'node_name': self.binding['node_id'], 'engine': self.connector.config['engine'],
               'mode': 'attached', 'state': 'ready', 'binding': self.binding,
               'config_revision': self.connector.revision, 'models': models,
               'revision': (existing or {}).get('revision', 0)}
        if existing is None or any(existing.get(k) != row[k] for k in ('node_id', 'state', 'binding', 'config_revision', 'models')):
            self.hub.request('PUT', '/' + deployment_id, row)
        self.remember(offered)
        self.routes({m['id'] for m in models})

    def routes(self, published):
        deployment_id, node = self.value['deployment_id'], self.binding['node_id']
        existing = {r['route_id']: r for r in self.hub.request('GET', '/routes')['routes']}
        for route_id, chain in self.value['routes'].items():
            route = existing.get(route_id)
            if route is None:
                candidates = [{'deployment_id': deployment_id, 'model_id': m} for m in chain if m in published]
                if not candidates:
                    continue
                route = {'route_id': route_id, 'name': route_id.removeprefix('tier-').capitalize(),
                         'candidates': candidates, 'allowed_nodes': [node], 'allowed_compute': ['provider'],
                         'allowed_billing': ['provider'], 'transport': 'relay_allowed', 'fallback': 'failover',
                         'selection': 'ordered', 'requires': {'operation': 'text', 'tools': True}, 'revision': 0}
            elif node not in route['allowed_nodes'] and all(c['deployment_id'] == deployment_id for c in route['candidates']):
                route = {**route, 'allowed_nodes': [node]}  # followed this service to its new node
            else:
                continue
            self.hub.request('PUT', '/routes/' + route_id, route)
        # Any other route over only this service follows it to its node too.
        for route_id, route in existing.items():
            if (route_id not in self.value['routes'] and route['candidates']
                    and all(c['deployment_id'] == deployment_id for c in route['candidates'])
                    and node not in route['allowed_nodes']):
                self.hub.request('PUT', '/routes/' + route_id, {**route, 'allowed_nodes': [node]})

    def run(self, stop=None):
        delay = 2
        while stop is None or not stop.is_set():
            time.sleep(delay)
            try:
                self.sync()
                self.log('model directory: registered ' + self.value['deployment_id'])
                return True
            except (RuntimeError, ValueError, URLError, OSError, KeyError, TypeError) as error:
                self.log(f'model directory: {error}; retrying')
                delay = min(delay * 2, 60)
        return False

    def start(self):
        thread = threading.Thread(target=self.run, name='model-directory', daemon=True)
        thread.start()
        return thread
