"""Owned CPU speech engine; fixed image, offline weights and actual resident load."""
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.request import urlopen

from speech_models import model, source, verify_directory


# Upstream's distribution version is 0.1.0 for this release, so that metadata
# alone cannot validate our adapter contract. The recipe pins the complete image;
# these source checks also reject accidental use of another provisioned runtime.
SOURCE_HASHES = {
    'main.py': 'ed5bfeb628d59aba68339f0dc915361b244d7936e6d81673b7e30fde7f0185b0',
    'config.py': '9a2abc30f4c681960877fea8cb14f91ea421a7f616c1922b01bd694750e6d851',
    'executors/kokoro.py': 'd843b8b2c9090c82fcea38301c172b3d6f49376ac64d2ac9839eaa44cfde5a98',
    'executors/whisper.py': '52515e6f4786885f4fd70b731bcc2a58b16fc6af55efb97731225222b32a5a02',
    'executors/shared/base_model_manager.py': 'a0fff5178e8a7c4be1ff906cde8dee03d56e573d09ab7beafabf744221bf2e5b',
}


def configuration():
    config = json.loads(Path(__file__).with_name('engine-config.json').read_text())
    selected = model(config['model_recipe_id'])
    if (config['resources']['devices'] != [] or config['resources']['memory_bytes'] < selected['minimum_memory_bytes']
            or config['load_policy'] != 'resident' or config['keep_alive_seconds'] != 0 or config['parallel'] != 1):
        raise ValueError('Invalid owned CPU speech resource or lifetime configuration')
    return config, selected


def verify_engine():
    spec = importlib.util.find_spec('speaches')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('The pinned speech engine is not installed')
    root = Path(next(iter(spec.submodule_search_locations)))
    for name, digest in SOURCE_HASHES.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError('The speech engine does not match its pinned recipe')


def environment(inherited, weights='/fleet/weights', state='/fleet/state'):
    # Do not inherit cloud credentials, external proxies or upstream model URLs.
    env = {k: v for k, v in inherited.items() if k in {'PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH'}}
    env.update(HOME=state, HF_HOME=state + '/huggingface', HF_HUB_CACHE=weights + '/hub',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               DO_NOT_TRACK='1', DISABLE_TELEMETRY='1', GRADIO_ANALYTICS_ENABLED='False',
               ENABLE_UI='false', PRELOAD_MODELS='[]', LOG_LEVEL='warning',
               WHISPER__INFERENCE_DEVICE='cpu', WHISPER__COMPUTE_TYPE='int8',
               WHISPER__CPU_THREADS='4', WHISPER__NUM_WORKERS='1',
               OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4', TOKENIZERS_PARALLELISM='false',
               STT_MODEL_TTL='-1', TTS_MODEL_TTL='-1', VAD_MODEL_TTL='0',
               UNSTABLE_ORT_OPTS__EXCLUDE_PROVIDERS='["CUDAExecutionProvider","TensorrtExecutionProvider","AzureExecutionProvider"]')
    return env


def create_app():
    _, selected = configuration()
    verify_engine()
    verify_directory('/fleet/weights', selected)
    from speaches.main import create_app as upstream_app
    from speaches.dependencies import get_executor_registry
    from speaches.routers.utils import get_model_card_data_or_raise
    app = upstream_app()
    # This owned engine exposes only its declared speech capabilities. Remote
    # download/delete, proxy chat, realtime and unbudgeted model-load APIs are
    # absent; its immutable cache contains exactly the selected model.
    paths = {'/health'} | ({'/v1/audio/voices', '/v1/audio/models', '/v1/audio/speech'}
                           if selected['operation'] == 'speech' else {'/v1/audio/transcriptions'})
    app.router.routes = [route for route in app.router.routes if getattr(route, 'path', '') in paths]
    observed = {'manager': None, 'executor': None}
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            registry = get_executor_registry()
            card = get_model_card_data_or_raise(selected['model'])
            executor = next((entry for entry in registry.all_executors()
                             if entry.can_handle_model(selected['model'], card)), None)
            if executor is None:
                raise ValueError('The pinned engine cannot load this speech model')
            # PRELOAD_MODELS merely downloads in this upstream release. Entering
            # the model context performs the real CPU allocation before ready.
            with executor.model_manager.load_model(selected['model']):
                pass
            observed['manager'] = executor.model_manager
            observed['executor'] = executor
            try:
                yield
            finally:
                observed['manager'] = None
                observed['executor'] = None

    app.router.lifespan_context = lifespan

    @app.get('/v1/models')
    def list_models():
        # The upstream catalog includes built-in VAD even when it is not a
        # published speech model. Only advertise this deployment's pinned model.
        executor = observed['executor']
        models = executor.model_registry.list_local_models() if executor is not None else []
        return dict(object='list', data=[entry.model_dump() for entry in models if entry.id == selected['model']])

    @app.get('/fleet/model-state')
    def model_state():
        manager = observed['manager']
        loaded = manager.loaded_models.get(selected['model']) if manager is not None else None
        return dict(model=selected['model'], model_recipe_id=selected['id'],
                    sha256=source(selected)['sha256'], revision=selected['revision'],
                    loaded=loaded is not None and loaded.model is not None, operation=selected['operation'])
    return app


def main():
    _, selected = configuration()
    port = int(os.environ.get('PANTHEON_PORT_HTTP', '8000'))
    if not 0 < port < 65536:
        raise ValueError('Invalid speech engine port')
    if sys.argv[1:] == ['ready']:
        with urlopen(f'http://127.0.0.1:{port}/fleet/model-state', timeout=2) as response:
            state = json.load(response)
        if (state.get('model') != selected['model'] or state.get('sha256') != source(selected)['sha256']
                or state.get('loaded') is not True):
            raise ValueError('The owned speech model has not finished loading')
        print('{"status":"succeeded"}')
        return
    if sys.argv[1:] != ['start']:
        raise ValueError('Unsupported speech engine action')
    verify_engine()
    verify_directory('/fleet/weights', selected)
    env = environment(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).parent)
    argv = [sys.executable, '-m', 'uvicorn', 'speaches_runtime:create_app', '--factory',
            '--host', '127.0.0.1' if 'PANTHEON_PORT_HTTP' in os.environ else '0.0.0.0',
            '--port', str(port), '--workers', '1', '--log-level', 'warning']
    os.execve(sys.executable, argv, env)


if __name__ == '__main__':
    main()
