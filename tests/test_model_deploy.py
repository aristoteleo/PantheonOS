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

    async def model_operations(self, dep, action='status', job_id='', operation='', artifact_job_id='',
                               template='', parameters=None):
        self.calls.append(('models', action, operation, template, parameters))
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
    imports = [c for c in manager.calls if c[:3] == ('models', 'submit', 'import')]
    assert imports and imports[0][3] == '' and imports[0][4] is None  # catalog GGUF: template from GGUF metadata
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


def test_search_hf_lists_chat_models_with_gpu_fit():
    bf16 = lambda n: {'total': n, 'parameters': {'BF16': n}}
    rows = [
        dict(id='Qwen/Qwen3-8B', downloads=9, config=dict(architectures=['Qwen3ForCausalLM'], model_type='qwen3'),
             safetensors=bf16(8_190_735_360)),
        dict(id='org/huge', config=dict(architectures=['Qwen3ForCausalLM']), safetensors=bf16(70_000_000_000)),
        dict(id='trl-internal-testing/tiny', config=dict(architectures=['Qwen2ForCausalLM']), safetensors=bf16(2_435_016)),
        dict(id='org/gated', gated='auto', config=dict(architectures=['LlamaForCausalLM'])),
        dict(id='org/noarch', config={}),
        dict(id='org/exotic', config=dict(architectures=['NotARealForCausalLM'])),
        dict(id='org/remote', config=dict(architectures=['Qwen3ForCausalLM'], auto_map={'a': 'b'})),
        dict(id='org/q-FP8', config=dict(architectures=['Qwen3ForCausalLM'], quantization_config={'quant_method': 'fp8'})),
        dict(id='org/q-AWQ', config=dict(architectures=['Qwen3ForCausalLM'], quantization_config={'quant_method': 'awq'})),
    ]
    seen = []

    def reply(request):
        seen.append(request.url)
        return httpx.Response(200, json=rows)
    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    results = {r['id']: r for r in asyncio.run(model_deploy.search_hf('qwen', gpu='A100-80GB', sort='trending', client=client))}
    # Only chat models in safetensors, in the requested order.
    params = seen[0].params
    assert params.get_list('filter') == ['conversational', 'safetensors'] and params['sort'] == 'trendingScore'
    assert 'trl-internal-testing/tiny' not in results  # toy checkpoints are skipped
    qwen = results['Qwen/Qwen3-8B']
    assert qwen['supported'] and qwen['fits'] and qwen['tools'] and qwen['reasoning']
    assert 20 << 30 < qwen['gpu_memory_needed'] < 22 << 30
    assert results['org/huge']['fits'] is False and 'GPU memory' in results['org/huge']['reason']
    assert 'Gated' in results['org/gated']['reason'] and 'No model architecture' in results['org/noarch']['reason']
    assert 'does not serve' in results['org/exotic']['reason'] and 'custom code' in results['org/remote']['reason']
    assert 'H100' in results['org/q-FP8']['reason'] and 'AWQ' in results['org/q-AWQ']['reason']


def test_ollama_search_page_is_parsed():
    page = '''<li class="x"><a href="/library/qwen3" class="g"><div><h2><span>qwen3</span></h2>
      <p class="max-w-lg">Qwen3 models.</p></div><div><span class="inline-flex rounded-md bg-indigo">tools</span>
      <span class="inline-flex rounded-md bg-indigo">thinking</span><span class="inline-flex rounded-md bg-blue">0.6b</span>
      <span class="inline-flex rounded-md bg-blue">30b</span><span x-test-pull-count>21M</span></div></a></li>
      <li class="x"><a href="/library/cloudy"><p class="d">Cloud.</p><span class="rounded-md">cloud</span></a></li>'''
    results = model_deploy._parse_ollama_search(page, 10)
    assert results[0] == dict(name='qwen3', description='Qwen3 models.', tags=['0.6b', '30b'],
                              capabilities=['tools', 'thinking'], pulls='21M', cloud_only=False)
    assert results[1]['cloud_only'] is True


def registry(layers, blobs):
    manifest = json.dumps(dict(schemaVersion=2, layers=layers)).encode()

    def handler(request):
        if '/manifests/' in request.url.path:
            return httpx.Response(200, content=manifest)
        digest = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(200, content=blobs[digest])
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_resolve_ollama_pins_manifest_layers_and_verifies_small_blobs():
    import hashlib
    template = '{{ if .Tools }}tools{{ end }}{{ .Think }}'
    params = json.dumps({'stop': ['<|im_end|>'], 'temperature': 0.6, 'num_ctx': 40960, 'unknown': 1}).encode()
    digests = {k: 'sha256:' + hashlib.sha256(v).hexdigest() for k, v in (('t', template.encode()), ('p', params))}
    layers = [dict(mediaType='application/vnd.ollama.image.model', digest='sha256:' + 'e' * 64, size=522640096),
              dict(mediaType='application/vnd.ollama.image.template', digest=digests['t'], size=len(template)),
              dict(mediaType='application/vnd.ollama.image.params', digest=digests['p'], size=len(params))]
    blobs = {digests['t']: template.encode(), digests['p']: params}
    entry = asyncio.run(model_deploy.resolve_ollama('qwen3:0.6b', client=registry(layers, blobs)))['model']
    assert entry['id'].startswith('ollama-qwen3-0-6b-') and entry['display_name'] == 'qwen3:0.6b'
    assert entry['source']['url'] == 'https://registry.ollama.ai/v2/library/qwen3/blobs/sha256:' + 'e' * 64
    assert entry['source']['sha256'] == 'e' * 64 and entry['parameters'] == {'stop': ['<|im_end|>'], 'temperature': 0.6}
    assert entry['capabilities']['tools'] and entry['capabilities']['reasoning'] and entry['context_length'] == 40960
    assert model_deploy._ollama_model(entry) is entry
    tampered = {digests['t']: b'other', digests['p']: params}
    with pytest.raises(ValueError, match='digest'):
        asyncio.run(model_deploy.resolve_ollama('qwen3:0.6b', client=registry(layers, tampered)))
    with pytest.raises(ValueError, match='cloud'):
        asyncio.run(model_deploy.resolve_ollama('qwen3:cloud', client=registry(layers[1:], blobs)))


def test_import_settings_are_bounded():
    from pantheon.models.managed import module
    control = module('model_control').ModelControl
    assert control.import_settings('t', {'stop': ['x'], 'top_k': 20})[1] == {'stop': ['x'], 'top_k': 20}
    for bad in ({'num_gpu': 99}, {'stop': 'x'}, {'top_k': 1.5}):
        with pytest.raises(ValueError):
            control.import_settings('t', bad)
    with pytest.raises(ValueError):
        control.import_settings('x' * (33 << 10), {})
