"""A Fleet-deployed Connector registers itself in the owner's Hub model directory."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1] / 'apps/model-service'


@pytest.fixture
def directory(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location('connector_directory', ROOT / 'directory.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop('server', None)


class FakeHub:
    def __init__(self):
        self.rows, self.routes, self.writes = {}, {}, []

    def request(self, method, path, body=None):
        self.writes.append((method, path)) if method == 'PUT' else None
        if path == '':
            return {'deployments': list(self.rows.values())}
        if path == '/routes':
            return {'routes': list(self.routes.values())}
        if path.startswith('/routes/'):
            assert body['revision'] == self.routes.get(body['route_id'], {}).get('revision', 0)
            self.routes[body['route_id']] = {**body, 'revision': body['revision'] + 1}
            return self.routes[body['route_id']]
        current = self.rows.get(path[1:])
        assert body['revision'] == (current or {}).get('revision', 0)
        self.rows[path[1:]] = {**body, 'revision': body['revision'] + 1}
        return self.rows[path[1:]]


def connector(tmp_path, offered):
    return SimpleNamespace(data=tmp_path, revision='r' * 64, config={'engine': 'api'},
                           activity_status=lambda: {'accepting': True},
                           discover=lambda: {'models': [{'id': m, 'reported': r} for m, r in offered.items()]})


def configuration(generation=2, node='brain'):
    return SimpleNamespace(node_id=node, instance_id='i-conn', revision='a' * 64, generation=generation)


VALUE = {'deployment_id': 'platform', 'name': 'Platform models',
         'models': [{'id': 'claude', 'required': True},
                    {'id': 'glm', 'suggested': {'context': 128000, 'tools': True}},
                    {'id': 'no-tools', 'suggested': {'context': 8000, 'tools': False}}],
         'routes': {'tier-high': ['claude', 'glm'], 'tier-low': ['missing']}}
OFFERED = {'claude': {'context': 200000, 'tools': True}, 'glm': {}, 'no-tools': {}}


def registration(directory, tmp_path, hub, **kw):
    r = directory.Registration(connector(tmp_path, OFFERED), configuration(**kw), VALUE,
                               SimpleNamespace(endpoint='https://hub.test/api/model-services', key='k'), log=lambda _: None)
    r.hub = hub
    return r


def test_registers_running_instance_catalog_and_tier_routes(directory, tmp_path):
    hub = FakeHub()
    registration(directory, tmp_path, hub).sync()
    row = hub.rows['platform']
    assert row['binding'] == {'node_id': 'brain', 'instance_id': 'i-conn', 'revision': 'a' * 64, 'generation': 2,
                              'component': 'backend', 'port': 'http'}
    assert row['state'] == 'ready' and row['config_revision'] == 'r' * 64
    assert [m['id'] for m in row['models']] == ['claude', 'glm']  # no-tools is not an Agent model
    route = hub.routes['tier-high']
    assert route['name'] == 'High' and [c['model_id'] for c in route['candidates']] == ['claude', 'glm']
    assert route['allowed_nodes'] == ['brain'] and 'tier-low' not in hub.routes


def test_restart_keeps_owner_choices_and_follows_the_new_node(directory, tmp_path):
    hub = FakeHub()
    registration(directory, tmp_path, hub).sync()
    # The owner withdraws glm, renames the service and edits the route.
    row = hub.rows['platform']
    hub.rows['platform'] = {**row, 'name': 'Mine', 'models': [m for m in row['models'] if m['id'] != 'glm']}
    hub.routes['tier-high']['candidates'] = hub.routes['tier-high']['candidates'][:1]
    registration(directory, tmp_path, hub, generation=4, node='sandbox').sync()
    row = hub.rows['platform']
    assert [m['id'] for m in row['models']] == ['claude'] and row['name'] == 'Mine'
    assert row['binding']['generation'] == 4 and row['node_id'] == 'sandbox'
    route = hub.routes['tier-high']
    assert len(route['candidates']) == 1 and route['allowed_nodes'] == ['sandbox']


def test_unchanged_registration_writes_nothing(directory, tmp_path):
    hub = FakeHub()
    registration(directory, tmp_path, hub).sync()
    hub.writes.clear()
    registration(directory, tmp_path, hub).sync()
    assert hub.writes == []


def test_missing_required_model_is_retried_not_published(directory, tmp_path):
    hub = FakeHub()
    r = directory.Registration(connector(tmp_path, {'glm': {}}), configuration(), VALUE,
                               SimpleNamespace(endpoint='https://hub.test/api/model-services', key='k'), log=lambda _: None)
    r.hub = hub
    with pytest.raises(RuntimeError, match='required model claude'):
        r.sync()
    assert hub.rows == {}


def test_credential_must_be_bound_to_the_directory_api(directory, tmp_path):
    with pytest.raises(ValueError, match='api/model-services'):
        directory.Registration(connector(tmp_path, OFFERED), configuration(), VALUE,
                               SimpleNamespace(endpoint='https://hub.test/v1', key='k'))
