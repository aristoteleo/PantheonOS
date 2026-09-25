"""Generate immutable Fleet components from constrained model-engine recipes."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile

from pantheon.apps.registry import BUILTIN_ROOT


def module(name):
    spec = importlib.util.spec_from_file_location('fleet_model_' + name, BUILTIN_ROOT / 'model-service' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def engines():
    return module('engines')


def validate(value, target):
    if not isinstance(value, dict) or set(value) - {'recipe_id', 'context_length', 'parallel', 'keep_alive_seconds', 'resources', 'model_artifact_sha256', 'model_recipe_id', 'load_policy', 'tensor_parallel_size', 'model_manifest'} or not {'recipe_id', 'context_length', 'parallel', 'keep_alive_seconds', 'resources'} <= set(value):
        raise ValueError('Specify the engine recipe, context, concurrency, lifetime and memory budget')
    selected = engines().recipe(value['recipe_id'], target=target)
    if 'model_manifest' in value and selected.get('runtime') != 'preinstalled':
        raise ValueError('Custom model manifests run on the node-provided SGLang only')
    if selected['engine'] not in {'ollama', 'lmstudio', 'sglang', 'speaches'}:
        raise ValueError('This engine does not yet have a managed launch recipe')
    for key, low, high in [('context_length', 512, 1048576), ('parallel', 1, 16), ('keep_alive_seconds', 0, 86400)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f'Invalid {key}')
    policy = value.get('load_policy', 'manual')
    if not isinstance(policy, str) or policy not in {'manual', 'on_demand', 'warm', 'resident'}:
        raise ValueError('Choose an explicit model loading policy')
    if ((policy == 'warm' and value['keep_alive_seconds'] < 1)
            or (policy in {'on_demand', 'resident'} and value['keep_alive_seconds'] != 0)):
        raise ValueError('Warm models need a positive idle TTL; on-demand and resident use zero')
    if selected['engine'] == 'lmstudio' and (value['parallel'] != 1 or (policy == 'manual' and value['keep_alive_seconds'] < 1)):
        raise ValueError('The pinned llmster recipe requires parallel=1; manual loading needs a positive idle TTL')
    diffusion = selected.get('operation') in {'image', 'video'}
    if diffusion:
        module('diffusion_models').model(value.get('model_recipe_id'))
        if (value.get('model_recipe_id') != selected['model_recipe_id'] or value.get('model_artifact_sha256')
                or value['parallel'] != 1 or value['context_length'] != 512
                or value['keep_alive_seconds'] != 0 or policy != 'resident'):
            raise ValueError('Managed diffusion requires its pinned resident model and one request at a time')
    elif selected.get('runtime') == 'preinstalled':
        # Node-provided SGLang serving a pinned catalog LLM (Modal GPU nodes).
        manifest = value.get('model_manifest')
        if manifest is not None and (not isinstance(manifest, dict) or manifest.get('id') != value.get('model_recipe_id')
                                     or len(json.dumps(manifest)) > 64 << 10):
            raise ValueError('A custom model manifest must match its model id and stay under 64 KB')
        llm = module('llm_models').model(manifest or value.get('model_recipe_id'))
        if value.get('model_artifact_sha256'):
            raise ValueError('Catalog language models are prepared from their pinned manifest')
        if target != 'linux-amd64' or value['keep_alive_seconds'] != 0 or policy not in {'manual', 'resident'}:
            raise ValueError('This SGLang recipe runs resident on Linux NVIDIA; stop the service to unload it')
        if value['context_length'] > llm['maximum_context_length']:
            raise ValueError('Context length exceeds what this model supports')
    elif selected['engine'] == 'sglang':
        if not re.fullmatch('[a-f0-9]{64}', str(value.get('model_artifact_sha256', ''))):
            raise ValueError('SGLang requires the SHA256 of a safetensors.tar.gz model bundle')
        if target != 'linux-amd64' or value['keep_alive_seconds'] != 0 or policy not in {'manual', 'resident'}:
            raise ValueError('This SGLang recipe runs resident on Linux NVIDIA; stop the service to unload it')
    elif value.get('model_artifact_sha256'):
        raise ValueError('This recipe imports models after engine startup')
    resources = value['resources']
    if not isinstance(resources, dict) or set(resources) != {'memory_bytes', 'devices'}:
        raise ValueError('Declare system memory and the exact accelerator budget')
    if type(resources['memory_bytes']) is not int or not 256 << 20 <= resources['memory_bytes'] <= 1 << 50:
        raise ValueError('Declare a system memory budget of at least 256 MiB')
    if selected['engine'] == 'speaches':
        if 'tensor_parallel_size' in value:
            raise ValueError('Tensor parallelism requires the SGLang text recipe')
        speech = module('speech_models').model(value.get('model_recipe_id'))
        if (resources['devices'] != [] or target != 'linux-amd64' or value['parallel'] != 1
                or value['context_length'] != 512 or policy != 'resident' or value['keep_alive_seconds'] != 0):
            raise ValueError('This CPU speech recipe requires one resident model, parallel=1, context_length=512 and no accelerators')
        if resources['memory_bytes'] < speech['minimum_memory_bytes']:
            raise ValueError('The speech model and engine exceed this system memory budget')
        return json.loads(json.dumps(value))
    if value.get('model_recipe_id') and not diffusion and selected.get('runtime') != 'preinstalled':
        raise ValueError('Pinned speech models require a Speaches recipe')
    devices = resources['devices']
    tp = value.get('tensor_parallel_size', 1)
    if type(tp) is not int or tp not in {1, 2, 4, 8}:
        raise ValueError('Tensor parallel size must be 1, 2, 4 or 8')
    if 'tensor_parallel_size' in value and (selected['engine'] != 'sglang' or diffusion):
        raise ValueError('Tensor parallelism requires the SGLang text recipe')
    # Ollama on Linux/Windows may run CPU-only: no accelerator lease, and the
    # engine is started with every GPU hidden.
    cpu_only = (devices == [] and tp == 1 and 'tensor_parallel_size' not in value
                and selected['engine'] == 'ollama' and target.startswith(('linux-', 'windows-')))
    if not isinstance(devices, list) or (len(devices) != tp and not cpu_only):
        raise ValueError('Declare exactly one distinct accelerator per tensor parallel rank')
    seen = set()
    for device in devices:
        if (not isinstance(device, dict) or set(device) != {'id', 'backend', 'memory_bytes', 'exclusive'}
                or not isinstance(device['id'], str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}', device['id'])
                or type(device['memory_bytes']) is not int or not 256 << 20 <= device['memory_bytes'] <= 1 << 50
                or type(device['exclusive']) is not bool):
            raise ValueError('Invalid accelerator resource declaration')
        if device['id'] in seen:
            raise ValueError('Tensor parallel ranks require distinct accelerator IDs')
        seen.add(device['id'])
        if tp > 1 and (target != 'linux-amd64' or not device['exclusive']):
            raise ValueError('Tensor parallelism requires exclusive NVIDIA GPUs on one Linux node')
        if diffusion and (resources['memory_bytes'] < selected['minimum_memory_bytes']
                or device['memory_bytes'] < selected['minimum_gpu_memory_bytes'] or not device['exclusive']):
            raise ValueError('The diffusion model needs its declared system/GPU budget and an exclusive device')
        if target == 'darwin-arm64':
            if device['id'] != 'apple-metal' or device['backend'] != 'metal' or device['memory_bytes'] > resources['memory_bytes']:
                raise ValueError('Apple unified memory must be included once in the system memory budget')
        elif target.startswith(('linux-', 'windows-')):
            if device['backend'] != 'cuda' or not device['id'].startswith('GPU-'):
                raise ValueError('This managed recipe currently supports NVIDIA CUDA on Linux/Windows')
        else:
            raise ValueError('Managed execution on this platform is not available yet')
    if selected.get('runtime') == 'preinstalled':
        llm = module('llm_models').model(value.get('model_manifest') or value['model_recipe_id'])
        if resources['memory_bytes'] < llm['minimum_memory_bytes']:
            raise ValueError('The model exceeds this deployment’s system memory budget')
        if not devices or any(d['memory_bytes'] < llm['minimum_gpu_memory_bytes'] for d in devices):
            raise ValueError('The model needs more GPU memory than this deployment declares')
    return json.loads(json.dumps({k: v for k, v in value.items() if v is not None}))


@contextmanager
def package(config, target):
    config = validate(config, target)
    system, arch = target.split('-')
    python = 'python' if system == 'windows' else 'python3'
    with tempfile.TemporaryDirectory(prefix='fleet-model-engine-') as temporary:
        root = Path(temporary)
        selected = engines().recipe(config['recipe_id'], target=target)
        for name in ('managed_engine.py', 'engines.py', 'engines.json', 'llmster_runtime.py', 'sglang_runtime.py', 'snapshots.py', 'speaches_runtime.py', 'speech_models.py', 'speech-models.json', 'pinned_models.py', 'diffusion_models.py', 'diffusion-models.json', 'sglang_diffusion_runtime.py', 'llm_models.py', 'llm-models.json'):
            shutil.copyfile(BUILTIN_ROOT / 'model-service' / name, root / name)
        # SGLang launchers read the exact GPU reservation from their config.
        with_resources = selected.get('runtime') == 'container' or (selected['engine'] == 'sglang' and selected.get('runtime') == 'preinstalled')
        (root / 'engine-config.json').write_text(json.dumps(config if with_resources else {k: v for k, v in config.items() if k != 'resources'}, sort_keys=True))
        # Same App identity shares a stable engine/weight cache. The dedicated
        # engine-<deployment> scope owns its processes and state separately.
        definition = dict(protocol=1, app_id='model-service', version='0.1.0',
            requires=dict(os=[system], arch=[arch], caps=['proc']), components=[dict(
                name='backend', runtime='process', argv=[python, '${PACKAGE}/managed_engine.py', 'start'],
                ports={'http': 0}, stop_seconds=30, resources=config['resources'],
                readiness=dict(argv=[python, '${PACKAGE}/managed_engine.py', 'ready'], timeout_seconds=30))])
        if selected.get('operation') in {'image', 'video'}:
            diffusion = module('diffusion_models')
            digest = diffusion.source(diffusion.model(config['model_recipe_id']))['sha256']
            definition['dependencies'] = dict(container_engine=dict(provider='docker', provision='never'))
            definition['components'] = [dict(name='backend', runtime='container', image=selected['image'],
                argv=['python3', '/fleet/package/sglang_diffusion_runtime.py', 'start'], ports={'http': 30000},
                mounts={'state': '/fleet/state'}, read_only_mounts={
                    'package': '/fleet/package', 'cache/diffusion-models/' + digest: '/fleet/weights'},
                stop_seconds=30, resources=config['resources'],
                readiness=dict(argv=['python3', '/fleet/package/sglang_diffusion_runtime.py', 'ready'], timeout_seconds=480))]
        elif selected['engine'] == 'sglang' and selected.get('runtime') == 'preinstalled':
            # The node image provides the pinned interpreter first on PATH (Fleet
            # admits only PATH names or package placeholders as executables);
            # sglang_runtime refuses any other SGLang version. Weights come from
            # the App cache snapshot, and the engine listens on loopback only.
            definition['components'] = [dict(name='backend', runtime='process',
                argv=[selected['command'], '${PACKAGE}/sglang_runtime.py', 'start'], ports={'http': 0},
                stop_seconds=60, resources=config['resources'],
                readiness=dict(argv=[selected['command'], '${PACKAGE}/sglang_runtime.py', 'ready'], timeout_seconds=600))]
        elif selected['engine'] == 'sglang':
            definition['dependencies'] = dict(container_engine=dict(provider='docker', provision='never'))
            definition['components'] = [dict(name='backend', runtime='container', image=selected['image'],
                argv=['python3', '/fleet/package/sglang_runtime.py', 'start'], ports={'http': 30000},
                mounts={'state': '/fleet/state'}, read_only_mounts={
                    'package': '/fleet/package', 'cache/snapshots/' + config['model_artifact_sha256']: '/fleet/weights'},
                stop_seconds=30, resources=config['resources'],
                readiness=dict(argv=['python3', '/fleet/package/sglang_runtime.py', 'ready'], timeout_seconds=480))]
        if selected['engine'] == 'speaches':
            speech = module('speech_models')
            digest = speech.source(speech.model(config['model_recipe_id']))['sha256']
            python = '/home/ubuntu/speaches/.venv/bin/python'
            definition['dependencies'] = dict(container_engine=dict(provider='docker', provision='never'))
            definition['components'] = [dict(name='backend', runtime='container', image=selected['image'],
                argv=[python, '/fleet/package/speaches_runtime.py', 'start'], ports={'http': 8000},
                run_as_owner=True,
                mounts={'state': '/fleet/state'}, read_only_mounts={
                    'package': '/fleet/package', 'cache/speech-models/' + digest: '/fleet/weights'},
                stop_seconds=30, resources=config['resources'],
                readiness=dict(argv=[python, '/fleet/package/speaches_runtime.py', 'ready'], timeout_seconds=180))]
        (root / 'fleet.json').write_text(json.dumps(definition, sort_keys=True))
        (root / 'app.json').write_text(json.dumps(dict(id='model-service', name='Managed model engine',
            version='0.1.0', apiVersion=2, entry={}, execution=dict(protocol=1, manifest='fleet.json'))))
        yield root
