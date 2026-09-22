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


def launch(config, record, total_gpu_bytes):
    if record['sha256'] != config['model_artifact_sha256']:
        raise ValueError('Model snapshot does not match this deployment')
    estimate = memory_estimate(record, config['context_length'], config['parallel'])
    budget = config['resources']['devices'][0]['memory_bytes']
    if estimate['estimated_bytes'] > budget:
        raise ValueError('Weights, KV cache and engine overhead exceed the declared GPU budget')
    if record['weights_bytes'] + (1 << 30) > config['resources']['memory_bytes']:
        raise ValueError('Model weights and loading overhead exceed the declared system memory budget')
    if total_gpu_bytes < budget:
        raise ValueError('The declared GPU budget exceeds this device memory')
    # Leave explicit space for CUDA/workspace outside SGLang's static pool.
    fraction = min(.85, (budget - (2 << 30)) / total_gpu_bytes)
    if fraction <= 0:
        raise ValueError('Insufficient GPU memory for an owned SGLang engine')
    return [sys.executable, '-m', 'sglang.launch_server', '--model-path', '/fleet/weights',
            '--served-model-name', 'fleet-snapshot-' + record['sha256'],
            '--host', '0.0.0.0', '--port', '30000', '--dtype', 'float16',
            '--load-format', 'safetensors', '--context-length', str(config['context_length']),
            '--max-running-requests', str(config['parallel']),
            '--max-total-tokens', str(config['context_length'] * config['parallel']),
            '--mem-fraction-static', str(round(fraction, 5)), '--disable-cuda-graph',
            '--attention-backend', 'triton', '--sampling-backend', 'pytorch', '--log-level', 'warning']


def main():
    config = json.loads(Path(__file__).with_name('engine-config.json').read_text())
    if version('sglang') != VERSION:
        raise ValueError('SGLang does not match its pinned recipe')
    # Containers use the fixed internal port, while a pre-provisioned runtime
    # can be supervised directly with a Runner-assigned process port.
    port = int(os.environ.get('PANTHEON_PORT_HTTP', '30000'))
    if not 0 < port < 65536:
        raise ValueError('Invalid engine port')
    if sys.argv[1:] == ['ready']:
        with urlopen(f'http://127.0.0.1:{port}/v1/models', timeout=2) as response:
            ids = [m['id'] for m in json.load(response)['data']]
            if ids != ['fleet-snapshot-' + config['model_artifact_sha256']]:
                raise ValueError('SGLang has not loaded this exact model')
        print('{"status":"succeeded"}')
        return
    if sys.argv[1:] != ['start']:
        raise ValueError('Unsupported engine action')
    record = json.loads(Path('/fleet/weights/snapshot.json').read_text())
    # UUID comes from the validated, immutable deployment and the Fleet device
    # reservation, never from a per-inference request.
    device = config['resources']['devices'][0]['id']
    result = subprocess.run(['nvidia-smi', '-i', device, '--query-gpu=memory.total',
                             '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10)
    total = int(result.stdout.strip()) << 20
    argv = launch(config, record, total)
    argv[argv.index('--port') + 1] = str(port)
    if 'PANTHEON_PORT_HTTP' in os.environ:
        argv[argv.index('--host') + 1] = '127.0.0.1'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HF_', 'HUGGING_FACE_', 'SGLANG_'))
           and k != 'PANTHEON_APP_RPC_TOKEN'}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               SGLANG_DISABLE_UPDATE_CHECK='1', HOME='/fleet/state')
    os.execve(sys.executable, argv, env)


if __name__ == '__main__':
    main()
