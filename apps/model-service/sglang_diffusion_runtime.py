"""Owned SGLang Diffusion entrypoint; fixed offline model, no startup downloads."""
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen

import diffusion_models
from engines import recipe

RECIPES = {'sglang-diffusion-0.5.20-linux-amd64', 'sglang-wan-0.5.20-linux-amd64'}


def launch(config, record, total_gpu_bytes):
    if config.get('recipe_id') not in RECIPES:
        raise ValueError('Unsupported owned diffusion recipe')
    selected = recipe(config['recipe_id'], target='linux-amd64')
    model = diffusion_models.model(config.get('model_recipe_id'))
    resources = config['resources']
    devices = resources['devices']
    if (model['id'] != selected['model_recipe_id'] or model['operation'] != selected['operation']
            or record['source'] != diffusion_models.source(model)
            or config.get('model_artifact_sha256') or config['context_length'] != 512
            or config['parallel'] != 1 or config.get('load_policy') != 'resident'
            or config['keep_alive_seconds'] != 0 or len(devices) != 1):
        raise ValueError('Diffusion requires the exact pinned resident model and one owned GPU')
    device = devices[0]
    if (resources['memory_bytes'] < selected['minimum_memory_bytes']
            or device['memory_bytes'] < selected['minimum_gpu_memory_bytes']
            or device['memory_bytes'] > total_gpu_bytes or device['backend'] != 'cuda'
            or device['exclusive'] is not True):
        raise ValueError('Diffusion exceeds the declared system/GPU budget or needs an exclusive CUDA device')
    snapshot = Path('/fleet/weights/hub') / ('models--' + model['model'].replace('/', '--')) / 'snapshots' / model['revision']
    argv = ['sglang', 'serve', '--model-path', str(snapshot),
            '--served-model-name', diffusion_models.served_name(model), '--backend', 'diffusers',
            '--num-gpus', '1', '--host', '0.0.0.0', '--port', '30000', '--warmup-mode', 'off',
            '--output-path', '/fleet/state/diffusion-output']
    if selected['operation'] == 'video':
        argv.append('--text-encoder-cpu-offload')
    return argv


def environment(inherited):
    # Do not pass connector credentials, provider keys, proxies or user-selected
    # HF/SGLang settings into the offline engine. Preserve container CUDA wiring.
    allowed = {'PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'CUDA_VISIBLE_DEVICES',
               'NVIDIA_VISIBLE_DEVICES', 'NVIDIA_DRIVER_CAPABILITIES', 'TMPDIR'}
    env = {k: v for k, v in inherited.items() if k in allowed}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               HF_HUB_CACHE='/fleet/weights/hub', HOME='/fleet/state',
               SGLANG_DISABLE_UPDATE_CHECK='1', TOKENIZERS_PARALLELISM='false')
    return env


def main():
    config = json.loads(Path(__file__).with_name('engine-config.json').read_text())
    if version('sglang') != '0.5.20':
        raise ValueError('SGLang does not match its pinned diffusion recipe')
    port = int(os.environ.get('PANTHEON_PORT_HTTP', '30000'))
    if not 0 < port < 65536:
        raise ValueError('Invalid engine port')
    model = diffusion_models.model(config['model_recipe_id'])
    if sys.argv[1:] == ['ready']:
        with urlopen(f'http://127.0.0.1:{port}/v1/models', timeout=2) as response:
            data = response.read((64 << 10) + 1)
            if len(data) > 64 << 10 or [m['id'] for m in json.loads(data)['data']] != [diffusion_models.served_name(model)]:
                raise ValueError('SGLang has not loaded this exact diffusion model')
        print('{"status":"succeeded"}')
        return
    if sys.argv[1:] != ['start']:
        raise ValueError('Unsupported engine action')
    record = diffusion_models.verify_directory('/fleet/weights', model)
    device = config['resources']['devices'][0]['id']
    result = subprocess.run(['nvidia-smi', '-i', device, '--query-gpu=memory.total',
                             '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10)
    argv = launch(config, record, int(result.stdout.strip()) << 20)
    executable = shutil.which('sglang')
    if not executable:
        raise ValueError('Pinned diffusion engine executable is missing')
    argv[0] = executable
    argv[argv.index('--port') + 1] = str(port)
    if 'PANTHEON_PORT_HTTP' in os.environ:
        argv[argv.index('--host') + 1] = '127.0.0.1'
    os.execve(executable, argv, environment(os.environ))


if __name__ == '__main__':
    main()
