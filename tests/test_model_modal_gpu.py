import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'apps' / 'model-service'
sys.path.insert(0, str(ROOT))
import engines  # noqa: E402
import llm_models  # noqa: E402
import sglang_runtime  # noqa: E402

GPU = dict(id='GPU-1cb0ac8b-8229-1e51-0a98-f2b216c131e8', backend='cuda', memory_bytes=72 << 30, exclusive=True)
CONFIG = dict(recipe_id='sglang-0.5.20-linux-amd64-node', model_recipe_id='qwen3.6-35b-a3b-fp8',
              context_length=65536, parallel=4, keep_alive_seconds=0, load_policy='resident',
              resources=dict(memory_bytes=56 << 30, devices=[GPU]))


def test_llm_catalog_pins_files_and_parsers():
    selected = llm_models.model('qwen3.6-35b-a3b-fp8')
    assert selected['tool_call_parser'] == 'qwen3_coder' and selected['reasoning_parser'] == 'qwen3'
    names = {f['name'] for f in selected['files']}
    assert 'chat_template.jinja' in names and '.gitattributes' not in names
    assert llm_models.served_name(selected).startswith('fleet-llm-')
    bad = json.loads(json.dumps(selected))
    bad['files'][0]['url'] = bad['files'][0]['url'].replace(selected['revision'], 'main')
    original = llm_models.catalog
    llm_models.catalog = lambda: [bad]
    try:
        with pytest.raises(ValueError):
            llm_models.model('qwen3.6-35b-a3b-fp8')
    finally:
        llm_models.catalog = original


def test_preinstalled_engine_requires_exact_interpreter_and_version(tmp_path):
    python = tmp_path / 'opt' / 'sglang' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_text('')
    selected = {**engines.recipe('sglang-0.5.20-linux-amd64-node', target='linux-amd64'), 'python': str(python)}
    assert engines.prepared(tmp_path, selected) is None and engines.requirement(selected)
    dist = tmp_path / 'opt' / 'sglang' / 'lib' / 'python3.12' / 'site-packages' / 'sglang-0.5.20.dist-info'
    dist.mkdir(parents=True)
    assert engines.prepared(tmp_path, selected) == python and engines.requirement(selected) == ''


def test_managed_package_runs_node_sglang_as_a_loopback_process(monkeypatch):
    monkeypatch.setitem(sys.modules, 'engines', engines)
    from pantheon.models.managed import package, validate
    assert validate(CONFIG, 'linux-amd64')['model_recipe_id'] == 'qwen3.6-35b-a3b-fp8'
    with package(CONFIG, 'linux-amd64') as directory:
        definition = json.loads((directory / 'fleet.json').read_text())
        component = definition['components'][0]
        assert component['runtime'] == 'process' and component['ports'] == {'http': 0}
        # Fleet admits only PATH names or package placeholders as process executables.
        assert component['argv'][:2] == ['python3', '${PACKAGE}/sglang_runtime.py']
        assert component['readiness']['argv'][0] == 'python3'
        assert 'dependencies' not in definition
        # Fleet refuses readiness probes outside 1..600 s at install time.
        assert 1 <= component['readiness']['timeout_seconds'] <= 600
        assert json.loads((directory / 'engine-config.json').read_text())['resources']['devices'] == [GPU]
        assert (directory / 'llm-models.json').exists()
    for change in (dict(model_recipe_id=None), dict(model_artifact_sha256='a' * 64), dict(load_policy='on_demand'),
                   dict(context_length=1 << 20),
                   dict(resources=dict(memory_bytes=56 << 30, devices=[{**GPU, 'memory_bytes': 32 << 30}]))):
        with pytest.raises(ValueError):
            validate({**CONFIG, **change}, 'linux-amd64')
    with pytest.raises(ValueError):
        validate(CONFIG, 'darwin-arm64')


def test_llm_launch_uses_catalog_parsers_and_declared_budget():
    selected = llm_models.model('qwen3.6-35b-a3b-fp8')
    argv = sglang_runtime.llm_launch(CONFIG, selected, '/w', 'fleet-llm-x', [80 << 30], 41234)
    flag = lambda name: argv[argv.index(name) + 1]
    assert flag('--tool-call-parser') == 'qwen3_coder' and flag('--reasoning-parser') == 'qwen3'
    assert flag('--host') == '127.0.0.1' and flag('--port') == '41234'
    assert float(flag('--mem-fraction-static')) <= .88 and flag('--context-length') == '65536'
    assert '--disable-cuda-graph' not in argv
    with pytest.raises(ValueError):
        sglang_runtime.llm_launch(CONFIG, selected, '/w', 'x', [40 << 30], 1)


class FakeClient:
    def __init__(self):
        self.launched, self.rows, self.route_calls, self.hub_calls = [], {}, [], []

    async def hub_request(self, method, path, data=None):
        self.hub_calls.append((method, path, data))
        if method == 'GET':
            return {'services': self.launched}
        if method == 'POST':
            self.launched.append(dict(service_id=data['service_id'], gpu=data['gpu'], expires_at='t'))
            return self.launched[-1]
        return {}

    async def deployments(self):
        return list(self.rows.values())

    async def routes(self):
        return []

    async def route_operation(self, action, **kw):
        self.route_calls.append((action, kw))


class FakeManager:
    def __init__(self, nodes):
        self.client = FakeClient()
        self.nodes = nodes
        self.resolver = type('R', (), {'_list_nodes': self._nodes})()
        self.weights_ready = False
        self.created = None

    async def _nodes(self, max_age=0):
        return self.nodes

    async def create_managed(self, dep, name, node_id, config):
        self.created = config
        row = dict(deployment_id=dep, node_id=node_id, state='draft', binding={'x': 1}, revision=1, managed=config, models=[])
        self.client.rows[dep] = row
        return row

    async def rpc(self, binding, method, args):
        if args['action'] == 'status':
            return {'ready': self.weights_ready}
        if args['action'] == 'jobs':
            return {'jobs': []}
        return {'job_id': args['model_id']}

    async def set_running(self, dep, running):
        self.client.rows[dep]['state'] = 'ready'

    async def publish(self, dep, models, revision):
        self.client.rows[dep]['models'] = models
        return self.client.rows[dep]


def test_modal_gpu_service_advances_node_weights_engine_publish(monkeypatch):
    from pantheon.models import modal_gpu
    tokens = []

    async def controller(path, body):
        tokens.append(path)
        return {'join_token': 'one-use'} if path == '/join-tokens' else {'ok': True}
    monkeypatch.setattr(modal_gpu, '_controller', controller)
    manager = FakeManager([])

    async def run():
        state = await modal_gpu.start(manager, 'qwen', gpu='H100')
        assert state['phase'] == 'starting_node' and tokens == ['/join-tokens']
        post = next(c for c in manager.client.hub_calls if c[0] == 'POST')
        assert post[2]['join_token'] == 'one-use' and post[2]['gpu'] == 'H100'
        manager.nodes = [dict(node_id='n_gpu', labels=['modal-gpu', 'svc-qwen'], state={'status': 'online'},
                              capability={'resources': {'accelerators': [dict(id=GPU['id'], backend='cuda',
                                                                               memory={'total_bytes': 80 << 30})]}})]
        state = await modal_gpu.advance(manager, 'qwen')
        assert state['phase'] == 'downloading_weights'
        device = manager.created['resources']['devices'][0]
        assert device['id'] == GPU['id'] and device['memory_bytes'] == (80 << 30) * 9 // 10
        manager.weights_ready = True
        assert (await modal_gpu.advance(manager, 'qwen'))['phase'] == 'starting_engine'
        await asyncio.sleep(0)
        state = await modal_gpu.advance(manager, 'qwen')
        assert state['phase'] == 'ready' and state['route'] == 'fleet-route://qwen'
        published = manager.client.rows['modal-qwen']['models'][0]
        assert published['tools'] is True and published['context'] == 131072 and published['compute'] == 'node'
        action, kw = manager.client.route_calls[-1]
        assert action == 'save' and kw['route']['allowed_nodes'] == ['n_gpu']
    asyncio.run(run())


def test_expired_modal_service_rows_are_settled_stopped(monkeypatch):
    from pantheon.models import modal_gpu
    revoked = []

    async def controller(path, body):
        revoked.append(body['node_id'])
        return {'ok': True}
    monkeypatch.setattr(modal_gpu, '_controller', controller)
    manager = FakeManager([dict(node_id='n_mac', labels=[], state={'status': 'online'})])
    manager.client.rows = {
        'modal-qwen36': dict(deployment_id='modal-qwen36', node_id='n_gone', state='ready', revision=3),
        'modal-live': dict(deployment_id='modal-live', node_id='n_gone2', state='ready', revision=1),
        'mac-ollama': dict(deployment_id='mac-ollama', node_id='n_gone3', state='ready', revision=1),
        'modal-onmac': dict(deployment_id='modal-onmac', node_id='n_mac', state='ready', revision=1)}
    saved = []

    async def save(row):
        saved.append(row)
        manager.client.rows[row['deployment_id']] = row
        return row
    manager.client.save = save
    manager.client.launched = [dict(service_id='live', gpu='H100')]
    settled = asyncio.run(modal_gpu.settle_expired(manager))
    # Only an ended Modal launch whose node is gone; never other services or live launches.
    assert settled == ['modal-qwen36'] and revoked == ['n_gone']
    assert manager.client.rows['modal-qwen36']['state'] == 'stopped'
    assert manager.client.rows['mac-ollama']['state'] == 'ready'


def test_fp8_catalog_model_refuses_gpus_without_fp8_kernels():
    # Live: Triton "fp8e4nv not supported" on an A100 (sm80) after a 5-minute start.
    from pantheon.models import modal_gpu
    selected = modal_gpu._catalog('qwen3.6-35b-a3b-fp8')
    assert modal_gpu._gpu_mismatch(selected, 'NVIDIA H100 80GB HBM3') == ''
    assert modal_gpu._gpu_mismatch(selected, 'NVIDIA L40S') == ''
    assert 'H100 or L40S' in modal_gpu._gpu_mismatch(selected, 'NVIDIA A100-SXM4-80GB')
    with pytest.raises(ValueError, match='H100 or L40S'):
        asyncio.run(modal_gpu.start(FakeManager([]), 'qwen', gpu='A100-80GB'))


def test_catalog_model_entry_is_accepted_only_unmodified(tmp_path):
    # Live: the SGLang launcher passes the catalog entry itself; it was refused as
    # "Custom language models need an hf- id" and the engine exited before readiness.
    entry = llm_models.model('qwen3.6-35b-a3b-fp8')
    assert llm_models.model(dict(entry)) == entry
    assert llm_models.prepared(tmp_path, entry) is None  # not downloaded yet, but valid
    tampered = dict(entry, revision='0' * 40)
    with pytest.raises(ValueError, match='pinned manifest'):
        llm_models.model(tampered)


def test_start_releases_only_this_deployments_failed_engines(monkeypatch):
    # Live: a failed engine kept its GPU lease, so the retry got "already reserved".
    from pantheon.models import manager as manager_module
    stopped = []

    class Lifecycle:
        def __init__(self, resolver):
            pass

        async def status(self, node):
            inst = lambda scope, state, gen, app='model-service': dict(
                scope=scope, state=state, generation=gen, digest='d' * 64, app_id=app)
            return {'instances': {
                'a': inst('engine-modal-x', 'failed', 3), 'b': inst('engine-modal-x', 'ready', 4),
                'c': inst('engine-other', 'failed', 1), 'd': inst('model-modal-x', 'failed', 1)}}

        async def submit(self, node, action, digest, *, scope, generation):
            stopped.append((action, scope, generation))
            return {'operation_id': 'op'}
    monkeypatch.setattr(manager_module, 'FleetLifecycle', Lifecycle)
    m = manager_module.ModelServiceManager.__new__(manager_module.ModelServiceManager)
    m.resolver = object()

    async def wait(node, op):
        return {}
    m.wait = wait
    asyncio.run(m.release_failed_engines(dict(deployment_id='modal-x', node_id='n')))
    assert stopped == [('stop', 'engine-modal-x', 3)]


def test_snapshot_identity_ignores_serving_settings():
    entry = llm_models.model('qwen3.6-35b-a3b-fp8')
    tweaked = dict(entry, context_length=4096, display_name='x', tool_call_parser='qwen25')
    assert llm_models.source(tweaked)['sha256'] == llm_models.source(entry)['sha256']
    assert llm_models.source(dict(entry, revision='0' * 40))['sha256'] != llm_models.source(entry)['sha256']
