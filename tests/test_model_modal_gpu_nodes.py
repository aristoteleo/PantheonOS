import asyncio

import pytest

from pantheon.models import modal_gpu

NODE = dict(node_id='n_gpu', labels=['modal-gpu', 'svc-node-lab'], state={'status': 'online'})


class Client:
    def __init__(self):
        self.calls, self.launched = [], []

    async def hub_request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if method == 'GET':
            return {'services': self.launched}
        if method == 'POST':
            self.launched.append(dict(service_id=data['service_id'], gpu=data['gpu']))
        return {}


class Manager:
    def __init__(self, nodes):
        self.client, self.nodes = Client(), nodes
        self.resolver = type('R', (), {'_list_nodes': self._nodes})()

    async def _nodes(self, max_age=0):
        return self.nodes


@pytest.fixture
def controller(monkeypatch):
    calls = []

    async def fake(path, body):
        calls.append((path, body))
        return {'join_token': 'one-use'} if path == '/join-tokens' else {'ok': True}
    monkeypatch.setattr(modal_gpu, '_controller', fake)
    return calls


def test_bare_node_launch_and_stop(controller):
    manager = Manager([])

    async def run():
        state = await modal_gpu.start_node(manager, 'Lab', gpu='L40S', lifetime_minutes=60)
        assert state == dict(service_id='node-lab', gpu='L40S', phase='starting_node', node_id=None)
        post = [c for c in manager.client.calls if c[0] == 'POST']
        assert post[0][2] == dict(service_id='node-lab', gpu='L40S', join_token='one-use', lifetime_minutes=60)
        # A second start is idempotent: no second token, no second launch.
        await modal_gpu.start_node(manager, 'lab')
        assert [c[0] for c in controller] == ['/join-tokens'] and len(post) == 1
        manager.nodes = [NODE]
        assert (await modal_gpu.start_node(manager, 'lab'))['node_id'] == 'n_gpu'
        assert (await modal_gpu.stop_node(manager, 'node-lab'))['phase'] == 'stopped'
        assert ('DELETE', '/api/model-services/modal-gpu/node-lab', None) in manager.client.calls
        assert controller[-1] == ('/revoke', {'node_id': 'n_gpu'})
    asyncio.run(run())


def test_stop_node_refuses_model_services_and_bad_gpu(controller):
    manager = Manager([])
    with pytest.raises(ValueError):
        asyncio.run(modal_gpu.stop_node(manager, 'qwen36'))
    with pytest.raises(ValueError):
        asyncio.run(modal_gpu.start_node(manager, 'x', gpu='T4'))
    assert controller == []


def test_inventory_exposes_labels():
    from apps.fleet.inventory import node_inventory
    nodes = node_inventory([dict(NODE, last_seen='2999-01-01T00:00:00Z', capability={})])['nodes']
    assert nodes[0]['labels'] == ['modal-gpu', 'svc-node-lab']
    assert node_inventory([dict(node_id='n', last_seen='x')])['nodes'][0]['labels'] == []


def test_engine_recipes_offer_node_sglang_only_on_modal_nodes():
    from pantheon.models.manager import ModelServiceManager

    class M(ModelServiceManager):
        def __init__(self, labels):
            self.labels = labels

        async def node(self, node_id, *, managed=False):
            return dict(node_id=node_id, labels=self.labels, capability=dict(os='linux', arch='amd64'))

    for labels, available in ((['modal-gpu'], True), ([], False)):
        result = asyncio.run(M(labels).engine_recipes('n'))
        recipe = next(r for r in result['recipes'] if r['id'] == 'sglang-0.5.20-linux-amd64-node')
        assert (recipe['unavailable_reason'] == '') is available
        assert result['llm_models'][0]['id'] == 'qwen3.6-35b-a3b-fp8'
        assert result['llm_models'][0]['capabilities']['tools'] is True


def test_llm_models_requires_a_started_sglang_connector():
    from pantheon.models.manager import ModelServiceManager
    calls = []

    class M(ModelServiceManager):
        def __init__(self, row):
            self.client = type('C', (), {'deployment': self._row})()
            self.row = row

        async def _row(self, deployment_id):
            return self.row

        async def rpc(self, binding, method, args):
            calls.append((method, args))
            return {'jobs': []}

    row = dict(state='draft', binding={'b': 1}, engine='sglang')
    assert asyncio.run(M(row).llm_models('modal-q', 'jobs')) == {'jobs': []}
    assert calls == [('llm_models', dict(action='jobs', model_id='', resume=False))]
    with pytest.raises(ValueError):
        asyncio.run(M({**row, 'state': 'stopped'}).llm_models('modal-q', 'jobs'))
    with pytest.raises(ValueError):
        asyncio.run(M(row).llm_models('modal-q', 'delete'))
