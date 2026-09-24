"""Fleet component entry point for a prepared, pinned Ollama installation.

This process becomes the engine (exec), so Fleet supervises the actual engine.
No daemonization, download, shell installer or changes to a user's Ollama app.
"""
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import urlopen

from engines import prepared, recipe


def configuration(path):
    value = json.loads(Path(path).read_text())
    if set(value) - {'load_policy'} != {'recipe_id', 'context_length', 'parallel', 'keep_alive_seconds'}:
        raise ValueError('Unexpected managed engine configuration')
    for key, low, high in [('context_length', 512, 1048576), ('parallel', 1, 16), ('keep_alive_seconds', 0, 86400)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError('Managed engine parameter is outside its supported range')
    selected = recipe(value['recipe_id'])
    if selected['engine'] not in {'ollama', 'lmstudio'}:
        raise ValueError('Unsupported managed engine')
    policy = value.get('load_policy', 'manual')
    if not isinstance(policy, str) or policy not in {'manual', 'on_demand', 'warm', 'resident'}:
        raise ValueError('Invalid model loading policy')
    if ((policy == 'warm' and value['keep_alive_seconds'] < 1)
            or (policy in {'on_demand', 'resident'} and value['keep_alive_seconds'] != 0)):
        raise ValueError('Invalid idle TTL for the model loading policy')
    if selected['engine'] == 'lmstudio' and (value['parallel'] != 1 or (policy == 'manual' and value['keep_alive_seconds'] < 1)):
        raise ValueError('The pinned llmster recipe requires one concurrent load and a positive idle TTL')
    return value, selected


def main():
    config, selected = configuration(Path(__file__).with_name('engine-config.json'))
    port = int(os.environ['PANTHEON_PORT_HTTP'])
    if not 0 < port < 65536:
        raise ValueError('Missing Fleet-assigned engine port')
    cache = Path(os.environ['PANTHEON_APP_CACHE'])
    scope = os.environ['PANTHEON_APP_SCOPE']
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}', scope):
        raise ValueError('Invalid managed deployment scope')
    if sys.argv[1:] == ['ready']:
        if selected['engine'] == 'lmstudio':
            from llmster_runtime import home_for, ready
            ready(home_for(cache, scope), port, selected['id'])
            print('{"status":"succeeded"}')
            return
        with urlopen(f'http://127.0.0.1:{port}/api/version', timeout=2) as response:
            if json.load(response).get('version') != selected['version']:
                raise ValueError('Engine version does not match the pinned recipe')
        print('{"status":"succeeded"}')
        return
    if sys.argv[1:] != ['start']:
        raise ValueError('Unsupported engine action')
    binary = prepared(cache, selected, scope)
    if not binary:
        raise ValueError('Prepare this engine in Model Services before starting it')
    if selected['engine'] == 'lmstudio':
        from llmster_runtime import start
        start(binary, cache, scope, config, selected, port)
        return
    models = cache / 'models' / 'ollama' / scope
    models.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('OLLAMA_') and k != 'PANTHEON_APP_RPC_TOKEN'}
    env.update(OLLAMA_HOST=f'127.0.0.1:{port}', OLLAMA_MODELS=str(models), OLLAMA_NO_CLOUD='1',
               OLLAMA_CONTEXT_LENGTH=str(config['context_length']), OLLAMA_NUM_PARALLEL=str(config['parallel']),
               OLLAMA_KEEP_ALIVE=str(-1 if config.get('load_policy') in {'resident', 'on_demand'} else config['keep_alive_seconds']),
               OLLAMA_MAX_LOADED_MODELS='1')
    if env.get('CUDA_VISIBLE_DEVICES') == '-1':
        # No accelerator lease: keep AMD/ROCm GPUs hidden as well as CUDA.
        env.update(HIP_VISIBLE_DEVICES='-1', ROCR_VISIBLE_DEVICES='-1')
    if os.name == 'nt':
        # Windows Ollama needs %USERPROFILE%, which Fleet's minimal env omits;
        # keep its state inside this instance. os.execve does not replace the
        # process on Windows (it spawns and exits), so Fleet would lose the
        # engine; run it as a child so readiness and taskkill /T track it.
        env.setdefault('USERPROFILE', env.get('HOME') or str(models))
        import subprocess
        raise SystemExit(subprocess.call([str(binary), 'serve'], env=env))
    os.execve(binary, [str(binary), 'serve'], env)


if __name__ == '__main__':
    main()
