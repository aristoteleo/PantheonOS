"""Pinned SGLang container entrypoint, using an offline, read-only model snapshot."""
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

from snapshots import memory_estimate

VERSION = '0.5.20'


def ready(port, model_sha256):
    # Model discovery is available before SGLang's startup warmup completes.
    # Its readiness endpoint checks ServerStatus.Up without submitting more work.
    with urlopen(f'http://127.0.0.1:{port}/ready', timeout=2) as response:
        if response.status != 200:
            raise ValueError('SGLang is still warming up')
    with urlopen(f'http://127.0.0.1:{port}/v1/models', timeout=2) as response:
        ids = [m['id'] for m in json.load(response)['data']]
        if ids != ['fleet-snapshot-' + model_sha256]:
            raise ValueError('SGLang has not loaded this exact model')


def _memory_limits(config, record, total_gpu_bytes, local_tp):
    if record['sha256'] != config['model_artifact_sha256']:
        raise ValueError('Model snapshot does not match this deployment')
    tp = config.get('tensor_parallel_size', 1)
    estimate = memory_estimate(record, config['context_length'], config['parallel'], tp)
    devices = config['resources']['devices']
    totals = [total_gpu_bytes] if type(total_gpu_bytes) is int else total_gpu_bytes
    if (len(devices) != local_tp or not isinstance(totals, list) or len(totals) != local_tp
            or len({d['id'] for d in devices}) != local_tp):
        raise ValueError('Declare one distinct GPU and physical memory measurement per rank')
    # Each worker may deserialize the full source before selecting its shard.
    if local_tp * (record['weights_bytes'] + (1 << 30)) > config['resources']['memory_bytes']:
        raise ValueError('Model weights and loading overhead exceed the declared system memory budget')
    fractions = []
    for device, total in zip(devices, totals):
        budget = device['memory_bytes']
        if estimate['estimated_bytes'] > budget:
            raise ValueError('Weights, KV cache and engine overhead exceed a declared GPU budget')
        if type(total) is not int or total < budget:
            raise ValueError('The declared GPU budget exceeds this device memory')
        fractions.append(min(.85, (budget - estimate['workspace_bytes']) / total))
    # The engine takes one fraction for all ranks. Verify it still accommodates
    # the model on every device when physical capacities/budgets differ.
    return estimate, totals, min(fractions)


def _launch(config, record, total_gpu_bytes, local_tp, static_fraction=None):
    estimate, totals, limit = _memory_limits(config, record, total_gpu_bytes, local_tp)
    fraction = int(limit * 100000) / 100000 if static_fraction is None else static_fraction
    if (type(fraction) not in (int, float) or not 0 < fraction <= limit):
        raise ValueError('Static memory fraction exceeds a declared GPU budget')
    if fraction <= 0 or any(fraction * total < estimate['weights_bytes'] + estimate['kv_bytes'] for total in totals):
        raise ValueError('Insufficient per-device static memory budget for this tensor parallel group')
    return [sys.executable, '-m', 'sglang.launch_server', '--model-path', '/fleet/weights',
            '--served-model-name', 'fleet-snapshot-' + record['sha256'],
            '--host', '0.0.0.0', '--port', '30000', '--dtype', 'float16',
            '--tensor-parallel-size', str(config.get('tensor_parallel_size', 1)), '--load-format', 'safetensors', '--context-length', str(config['context_length']),
            '--max-running-requests', str(config['parallel']),
            '--max-total-tokens', str(config['context_length'] * config['parallel']),
            '--mem-fraction-static', str(round(fraction, 5)), '--disable-cuda-graph',
            '--attention-backend', 'triton', '--sampling-backend', 'pytorch', '--log-level', 'warning']


def launch(config, record, total_gpu_bytes):
    return _launch(config, record, total_gpu_bytes, config.get('tensor_parallel_size', 1))


def llm_launch(config, selected, weights, served, totals, port):
    """Serving flags for a pinned catalog LLM on a node-provided SGLang.

    Parsers and limits come from the catalog; the static memory fraction is
    bounded by each declared GPU budget, never by a request.
    """
    devices = config['resources']['devices']
    tp = config.get('tensor_parallel_size', 1)
    if len(devices) != tp or len(totals) != tp or config['context_length'] > selected['maximum_context_length']:
        raise ValueError('Declare one GPU per rank and a context the model supports')
    fractions = []
    for device, total in zip(devices, totals):
        if device['memory_bytes'] < selected['minimum_gpu_memory_bytes'] or total < device['memory_bytes']:
            raise ValueError('The declared GPU budget does not fit this model on this device')
        fractions.append(min(.88, device['memory_bytes'] / total))
    argv = [sys.executable, '-m', 'sglang.launch_server', '--model-path', str(weights),
            '--served-model-name', served, '--host', '127.0.0.1', '--port', str(port),
            '--tensor-parallel-size', str(tp), '--context-length', str(config['context_length']),
            '--max-running-requests', str(config['parallel']),
            '--mem-fraction-static', str(int(min(fractions) * 1000) / 1000), '--log-level', 'warning']
    if selected.get('tool_call_parser'):
        argv += ['--tool-call-parser', selected['tool_call_parser']]
    if selected.get('reasoning_parser'):
        argv += ['--reasoning-parser', selected['reasoning_parser']]
    return argv


def llm_main(config, port):
    import llm_models
    selected = llm_models.model(config.get('model_manifest') or config['model_recipe_id'])
    served = llm_models.served_name(selected)
    if sys.argv[1:] == ['ready']:
        with urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
            if response.status != 200:
                raise ValueError('SGLang is still warming up')
        with urlopen(f'http://127.0.0.1:{port}/v1/models', timeout=2) as response:
            if [m['id'] for m in json.load(response)['data']] != [served]:
                raise ValueError('SGLang has not loaded this exact model')
        print('{"status":"succeeded"}')
        return
    if sys.argv[1:] != ['start']:
        raise ValueError('Unsupported engine action')
    cache = Path(os.environ['PANTHEON_APP_CACHE'])
    if not llm_models.prepared(cache, selected):
        raise ValueError('Prepare this model’s pinned weights before starting SGLang')
    weights = (cache / 'llm-models' / llm_models.source(selected)['sha256'] / 'hub'
               / ('models--' + selected['model'].replace('/', '--')) / 'snapshots' / selected['revision'])
    devices = [d['id'] for d in config['resources']['devices']]
    totals = []
    for device in devices:
        result = subprocess.run(['nvidia-smi', '-i', device, '--query-gpu=memory.total',
                                 '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10)
        totals.append(int(result.stdout.strip()) << 20)
    argv = llm_launch(config, selected, weights, served, totals, port)
    # Fleet sets HOME to this instance's private data directory.
    state = Path(os.environ['HOME'])
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HF_', 'HUGGING_FACE_', 'SGLANG_'))
           and k != 'PANTHEON_APP_RPC_TOKEN'}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               SGLANG_DISABLE_UPDATE_CHECK='1', HOME=str(state), CUDA_VISIBLE_DEVICES=','.join(devices))
    os.execve(sys.executable, argv, env)


def main():
    config = json.loads(Path(__file__).with_name('engine-config.json').read_text())
    if version('sglang') != VERSION:
        raise ValueError('SGLang does not match its pinned recipe')
    # Containers use the fixed internal port, while a pre-provisioned runtime
    # can be supervised directly with a Runner-assigned process port.
    port = int(os.environ.get('PANTHEON_PORT_HTTP', '30000'))
    if not 0 < port < 65536:
        raise ValueError('Invalid engine port')
    if config.get('model_recipe_id'):
        return llm_main(config, port)
    if sys.argv[1:] == ['ready']:
        ready(port, config['model_artifact_sha256'])
        print('{"status":"succeeded"}')
        return
    if sys.argv[1:] != ['start']:
        raise ValueError('Unsupported engine action')
    record = json.loads(Path('/fleet/weights/snapshot.json').read_text())
    # UUID comes from the validated, immutable deployment and the Fleet device
    # reservation, never from a per-inference request.
    devices = [d['id'] for d in config['resources']['devices']]
    totals = []
    for device in devices:
        result = subprocess.run(['nvidia-smi', '-i', device, '--query-gpu=memory.total',
                                 '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10)
        totals.append(int(result.stdout.strip()) << 20)
    argv = launch(config, record, totals)
    argv[argv.index('--port') + 1] = str(port)
    if 'PANTHEON_PORT_HTTP' in os.environ:
        argv[argv.index('--host') + 1] = '127.0.0.1'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HF_', 'HUGGING_FACE_', 'SGLANG_'))
           and k != 'PANTHEON_APP_RPC_TOKEN'}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               SGLANG_DISABLE_UPDATE_CHECK='1', HOME='/fleet/state', CUDA_VISIBLE_DEVICES=','.join(devices))
    os.execve(sys.executable, argv, env)


if __name__ == '__main__':
    main()
