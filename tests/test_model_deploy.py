import asyncio
import json

import httpx
import pytest

from pantheon.models import modal_gpu, model_deploy

GPU_UUID = 'GPU-1cb0ac8b-8229-1e51-0a98-f2b216c131e8'


def gpu_node(node_id='n_gpu', name='NVIDIA A100-SXM4-80GB', service='work'):
    return dict(node_id=node_id, labels=['modal', 'modal-gpu', 'svc-' + service], state={'status': 'online'},
                capability=dict(os='linux', arch='amd64', ram_gb=1024, resources=dict(accelerators=[dict(
                    id=GPU_UUID, name=name, backend='cuda', memory={'total_bytes': 80 << 30})])))


def cpu_node(node_id='n_cpu'):
    return dict(node_id=node_id, labels=[], state={'status': 'online'},
                capability=dict(os='linux', arch='amd64', ram_gb=64, resources=dict(accelerators=[])))


class Client:
    def __init__(self):
        self.launched, self.rows, self.posts, self.routes_saved = [], {}, [], []

    async def hub_request(self, method, path, data=None):
        if method == 'GET':
            return {'services': self.launched}
        if method == 'POST':
            self.posts.append(data)
            self.launched.append(dict(service_id=data['service_id'], gpu=data['gpu'], memory_gib=data.get('memory_gib')))
            return {}
        return {}

    async def deployments(self):
        return list(self.rows.values())

    async def routes(self):
        return []

    async def route_operation(self, action, **kw):
        self.routes_saved.append(kw)


class Manager:
    def __init__(self, nodes):
        self.client, self.nodes, self.created = Client(), nodes, None
        self.resolver = type('R', (), {'_list_nodes': self._nodes})()
        self.engine_prepared, self.weights_ready, self.imported = False, False, False
        self.calls = []

    async def _nodes(self, max_age=0):
        return self.nodes

    async def create_managed(self, dep, name, node_id, config):
        self.created = config
        self.client.rows[dep] = dict(deployment_id=dep, node_id=node_id, state='draft', binding={'b': 1},
                                     revision=1, managed=config, models=[])
        return self.client.rows[dep]

    async def rpc(self, binding, method, args):
        self.calls.append((method, args))
        if args['action'] == 'status':
            return {'ready': self.weights_ready}
        if args['action'] == 'jobs':
            return {'jobs': []}
        return {'job_id': args['model_id']}

    async def engines(self, dep, action='catalog', recipe_id='', resume=False):
        self.calls.append(('engines', action))
        if action == 'catalog':
            return {'recipes': [dict(id=self.created['recipe_id'], prepared=self.engine_prepared, source={'size': 10})]}
        return {'jobs': []} if action == 'jobs' else {'job_id': recipe_id}

    async def set_running(self, dep, running):
        self.client.rows[dep]['state'] = 'ready'

    async def artifacts(self, dep, action='list', job_id='', source=None, resume=False):
        self.calls.append(('artifacts', action))
        if action == 'list':
            return {'jobs': [dict(job_id=j, state='ready' if self.weights_ready else 'downloading', bytes_done=5)
                             for j in self.submitted]} if hasattr(self, 'submitted') else {'jobs': []}
        self.submitted = [job_id]
        return {'job_id': job_id}

    async def model_operations(self, dep, action='status', job_id='', operation='', artifact_job_id=''):
        self.calls.append(('models', action, operation))
        if action == 'submit':
            self.imported = True
            return {'job_id': job_id}
        sha = model_deploy.ollama_catalog()[1]['source']['sha256']
        return {'models': [{'id': f'fleet/{sha}:latest'}] if self.imported else [], 'jobs': []}

    async def publish(self, dep, models, revision):
        self.client.rows[dep]['models'] = models
        return self.client.rows[dep]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('PANTHEON_MODEL_DEPLOY_DIR', str(tmp_path / 'plans'))

    async def controller(path, body):
        return {'join_token': 'one-use'} if path == '/join-tokens' else {'ok': True}
    monkeypatch.setattr(modal_gpu, '_controller', controller)


def by_id(rows):
    return {r['id']: r for r in rows}


def test_options_explain_fits_per_gpu_and_cpu():
    a100 = asyncio.run(model_deploy.options(Manager([]), gpu='A100-80GB'))
    engines = {e['id']: e for e in a100['engines']}
    assert engines['sglang']['available'] and engines['ollama']['available'] and not engines['lmstudio']['available']
    sglang = by_id(a100['sglang_models'])
    assert not sglang['qwen3.6-35b-a3b-fp8']['fits'] and 'H100' in sglang['qwen3.6-35b-a3b-fp8']['reason']
    assert sglang['qwen3-30b-a3b-instruct-2507']['fits'] and sglang['qwen3-8b']['fits']
    l40s = by_id(asyncio.run(model_deploy.options(Manager([]), gpu='L40S'))['sglang_models'])
    assert not l40s['qwen3-30b-a3b-instruct-2507']['fits'] and l40s['qwen3.6-35b-a3b-fp8']['fits']
    cpu = asyncio.run(model_deploy.options(Manager([]), gpu='none'))
    assert not {e['id']: e for e in cpu['engines']}['sglang']['available']
    ollama = by_id(cpu['ollama_models'])
    assert ollama['qwen3-8b-q4km']['fits'] and not ollama['qwen3-30b-a3b-instruct-2507-q4km']['fits']
    node = asyncio.run(model_deploy.options(Manager([cpu_node()]), node_id='n_cpu'))
    assert not {e['id']: e for e in node['engines']}['sglang']['available']


def hf_transport(config, siblings, sha='a' * 40):
    def handler(request):
        path = request.url.path
        if path.startswith('/api/models/'):
            return httpx.Response(200, json={'sha': sha, 'siblings': siblings, 'cardData': {'license': 'apache-2.0'}})
        if path.endswith('/config.json'):
            return httpx.Response(200, json=config)
        return httpx.Response(200, content=b'{}')
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


SIBLINGS = [dict(rfilename='config.json'), dict(rfilename='tokenizer_config.json'), dict(rfilename='.gitattributes'),
            dict(rfilename='model.safetensors', lfs=dict(sha256='b' * 64, size=16 << 30))]


def test_resolve_hf_pins_manifest_detects_fp8_and_guesses_parsers():
    result = asyncio.run(model_deploy.resolve_hf('Qwen/Qwen3-30B-A3B-Instruct-2507', client=hf_transport(
        dict(model_type='qwen3_moe', max_position_embeddings=262144), SIBLINGS)))
    entry = result['model']
    assert entry['id'].startswith('hf-qwen-qwen3-30b-a3b-instruct-2507-') and entry['revision'] == 'a' * 40
    assert entry['tool_call_parser'] == 'qwen25' and entry['reasoning_parser'] == ''
    assert {f['name'] for f in entry['files']} == {'config.json', 'tokenizer_config.json', 'model.safetensors'}
    assert entry['supported_gpus'] == ['A100', 'H100', 'L40S'] and entry['context_length'] == 32768
    fp8 = asyncio.run(model_deploy.resolve_hf('org/m-FP8', client=hf_transport(
        dict(model_type='llama', quantization_config={'quant_method': 'fp8'}), SIBLINGS)))['model']
    assert fp8['supported_gpus'] == ['H100', 'L40S'] and fp8['tool_call_parser'] == 'llama3'
    unknown = asyncio.run(model_deploy.resolve_hf('org/m', client=hf_transport(dict(model_type='phi3'), SIBLINGS)))
    assert unknown['model']['capabilities']['tools'] is False and unknown['warnings']
    with pytest.raises(ValueError, match='trust_remote_code'):
        asyncio.run(model_deploy.resolve_hf('org/m', client=hf_transport(dict(auto_map={'x': 'y'}), SIBLINGS)))
    with pytest.raises(ValueError, match='safetensors'):
        asyncio.run(model_deploy.resolve_hf('org/m', client=hf_transport(dict(model_type='qwen2'), SIBLINGS[:3])))


def test_deploy_sglang_catalog_on_new_modal_machine_then_advances():
    manager = Manager([])
    state = asyncio.run(model_deploy.deploy(manager, dict(kind='modal', gpu='A100-80GB', lifetime_hours=2),
                                            'sglang', {'catalog_id': 'qwen3-30b-a3b-instruct-2507'}, 'qwen30'))
    assert state['deployment_id'] == 'modal-node-qwen30' and state['service_id'] == 'node-qwen30'
    assert state['phase'] == 'starting_node'
    assert manager.client.posts[0]['gpu'] == 'A100-80GB' and manager.client.posts[0]['lifetime_minutes'] == 120
    manager.nodes = [gpu_node(service='node-qwen30')]
    state = asyncio.run(model_deploy.status(manager, 'modal-node-qwen30'))
    assert state['phase'] == 'downloading_weights' and manager.created['model_recipe_id'] == 'qwen3-30b-a3b-instruct-2507'
    assert 'model_manifest' not in manager.created
    with pytest.raises(ValueError, match='H100 or L40S'):
        asyncio.run(model_deploy.deploy(manager, dict(kind='modal', gpu='A100-80GB'), 'sglang',
                                        {'catalog_id': 'qwen3.6-35b-a3b-fp8'}))


def test_deploy_custom_sglang_manifest_on_existing_modal_node():
    manager = Manager([gpu_node(name='NVIDIA H100 80GB HBM3', service='node-gpu')])
    manager.client.launched = [dict(service_id='node-gpu', gpu='H100', memory_gib=64)]
    entry = asyncio.run(model_deploy.resolve_hf('Qwen/Qwen3-8B', client=hf_transport(dict(model_type='qwen3'), SIBLINGS)))['model']
    state = asyncio.run(model_deploy.deploy(manager, dict(kind='node', node_id='n_gpu'), 'sglang', entry))
    assert state['deployment_id'] == 'modal-node-gpu' and state['phase'] == 'downloading_weights'
    assert manager.created['model_manifest']['id'] == entry['id'] == manager.created['model_recipe_id']
    prepare = next(args for method, args in manager.calls if method == 'llm_models' and args['action'] == 'prepare')
    assert prepare['manifest']['id'] == entry['id']
    with pytest.raises(ValueError, match='platform GPU'):
        asyncio.run(model_deploy.deploy(Manager([cpu_node()]), dict(kind='node', node_id='n_cpu'), 'sglang',
                                        {'catalog_id': 'qwen3-8b'}))


def run_ollama(manager, dep):
    phases = []
    for _ in range(12):
        state = asyncio.run(model_deploy.status(manager, dep))
        phases.append(state['phase'])
        if state['phase'] == 'preparing_engine':
            manager.engine_prepared = True
        if state['phase'] == 'downloading_weights':
            manager.weights_ready = True
        if state['ready']:
            return phases, state
    raise AssertionError(phases)


def test_deploy_ollama_on_gpu_node_and_cpu_node():
    manager = Manager([gpu_node(name='NVIDIA L40S', service='node-l40s')])
    manager.client.launched = [dict(service_id='node-l40s', gpu='L40S', memory_gib=64)]
    state = asyncio.run(model_deploy.deploy(manager, dict(kind='node', node_id='n_gpu'), 'ollama', {'catalog_id': 'qwen3-8b-q4km'}))
    assert state['deployment_id'] == 'modal-node-l40s'
    assert manager.created['recipe_id'] == 'ollama-0.34.2-linux-amd64'
    assert manager.created['resources']['devices'][0]['id'] == GPU_UUID
    phases, state = run_ollama(manager, 'modal-node-l40s')
    assert 'starting_engine' in phases and 'publishing' in phases and state['route'] == 'fleet-route://node-l40s'
    published = manager.client.rows['modal-node-l40s']['models'][0]
    assert published['tools'] is True and published['compute'] == 'node'
    cpu = Manager([cpu_node()])
    state = asyncio.run(model_deploy.deploy(cpu, dict(kind='node', node_id='n_cpu'), 'ollama', {'catalog_id': 'qwen3-8b-q4km'}, 'Small'))
    assert state['deployment_id'] == 'ollama-small' and cpu.created['resources']['devices'] == []
    assert cpu.created['resources']['memory_bytes'] >= model_deploy.ollama_catalog()[1]['min_memory_bytes']
    phases, state = run_ollama(cpu, 'ollama-small')
    assert state['model'].startswith('fleet-model://ollama-small/') and state['route'] is None


def test_custom_manifest_is_registered_on_the_node_and_found_by_id(monkeypatch, tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps' / 'model-service'))
    import llm_models
    entry = asyncio.run(model_deploy.resolve_hf('Qwen/Qwen3-8B', client=hf_transport(dict(model_type='qwen3'), SIBLINGS)))['model']
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(tmp_path))
    with pytest.raises(ValueError):
        llm_models.model(entry['id'])
    llm_models.register(entry)
    assert llm_models.model(entry['id']) == llm_models.model(entry)
    assert llm_models.served_name(llm_models.model(entry['id'])) == llm_models.served_name(entry)
    with pytest.raises(ValueError):
        llm_models.model({**entry, 'id': 'qwen3-8b'})  # custom entries cannot pose as catalog ids


def test_agent_modal_deploy_needs_user_approval(monkeypatch):
    from pantheon.internal.model_services_plugin import ModelServicesToolSet
    toolset = ModelServicesToolSet()
    assert {'model_options', 'deploy_model', 'deploy_status'} <= set(toolset.tool_functions)
    called = []

    async def deploy(*args):
        called.append(args)
        return {'phase': 'starting_node'}
    monkeypatch.setattr(model_deploy, 'deploy', deploy)

    async def manager():
        return Manager([])
    monkeypatch.setattr(toolset, '_m', manager)
    refused = asyncio.run(toolset.deploy_model('ollama', model_id='qwen3-8b-q4km', gpu='L40S'))
    assert refused['started'] is False and not called
    asyncio.run(toolset.deploy_model('ollama', model_id='qwen3-8b-q4km', gpu='L40S', user_confirmed=True))
    assert called[0][1] == {'kind': 'modal', 'gpu': 'L40S', 'lifetime_hours': 4}
    asyncio.run(toolset.deploy_model('ollama', model_id='qwen3-8b-q4km', node_id='n_cpu'))  # own node: no approval
    assert called[1][1] == {'kind': 'node', 'node_id': 'n_cpu'}
