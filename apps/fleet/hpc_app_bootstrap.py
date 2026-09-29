"""Job-local App adapters. No Fleet client, credentials or package installation."""
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
from urllib.request import Request, urlopen


def private_json(path, value):
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
    os.replace(tmp, path)


def main(config):
    if not os.environ.get('SLURM_JOB_ID', '').isdecimal():
        raise RuntimeError('This App requires a Slurm job')
    root = Path(os.environ['PANTHEON_HPC_WORKSPACE'])
    metadata = dict(kind=config['kind'], revision=os.environ['PANTHEON_HPC_SERVICE_REVISION'],
                    job_id=os.environ['SLURM_JOB_ID'])
    if config['kind'] == 'jupyterlab':
        from jupyterlab.labapp import LabApp
        from traitlets.config import Config
        token = secrets.token_urlsafe(32)
        runtime = root / '.jupyter-runtime'
        runtime.mkdir(mode=0o700, exist_ok=True)
        os.environ['JUPYTER_RUNTIME_DIR'] = str(runtime)
        settings = Config()
        settings.ServerApp.ip = '127.0.0.1'
        settings.ServerApp.port = int(os.environ['PORT'])
        settings.ServerApp.port_retries = 0
        settings.ServerApp.open_browser = False
        settings.ServerApp.root_dir = str(root)
        settings.ServerApp.allow_remote_access = True
        settings.ServerApp.trust_xheaders = True
        settings.IdentityProvider.token = token
        settings.ServerApp.log_level = 'ERROR'
        # Limit framing to the requesting Atrium origin. Keep token and XSRF
        # authentication; a different user on the shared node cannot log in.
        settings.ServerApp.tornado_settings = {
            'headers': {'Content-Security-Policy': "frame-ancestors 'self' " + config['atrium_origin']},
            'cookie_options': {'SameSite': 'None', 'Secure': True},
            'xsrf_cookie_kwargs': {'samesite': 'None', 'secure': True},
        }
        private_json(root / '.hpc-app.json', dict(metadata, token=token))
        LabApp.launch_instance(argv=[], config=settings)
        return

    # The engine and the existing stdlib Model Services connector share the
    # allocation. Only the connector's HTTP port is forwarded by Fleet.
    import server
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    argv = [arg.replace('${ENGINE_PORT}', str(port)).replace('${HOST}', '127.0.0.1')
            for arg in config['engine_argv']]
    env = dict(os.environ)
    cache = os.path.expandvars(config['model_cache'])
    if not os.path.isabs(cache) or '$' in cache:
        raise ValueError('Model cache must resolve to an absolute job-accessible directory')
    Path(cache).mkdir(parents=True, exist_ok=True, mode=0o700)
    env.update(OLLAMA_HOST=f'127.0.0.1:{port}', OLLAMA_MODELS=cache,
               HF_HOME=cache, XDG_CACHE_HOME=cache)
    child = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL)
    stop = threading.Event()
    def terminate(*_):
        stop.set()
        if child.poll() is None:
            child.terminate()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    http = None
    try:
        endpoint = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + config['startup_seconds']
        while not stop.is_set() and child.poll() is None:
            try:
                with urlopen(endpoint + '/v1/models', timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Model engine did not become ready before startup timeout')
            stop.wait(.5)
        if stop.is_set() or child.poll() is not None:
            raise RuntimeError('Model engine exited before readiness')
        if config['engine'] == 'ollama' and config.get('model_name'):
            # Ollama reuses its content-addressed cache. Never download models
            # in a login-node process or implicitly install the engine.
            request = Request(endpoint + '/api/pull', data=json.dumps({
                'name': config['model_name'], 'stream': False}).encode(),
                headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=max(1, deadline - time.monotonic())) as response:
                result = json.loads(response.read(65536))
                if result.get('error'):
                    raise RuntimeError('Ollama could not prepare the requested model')
        connector = server.Connector(root / '.model-connector')
        result = connector.configure({'engine': config['engine'], 'endpoint': endpoint})
        from http.server import ThreadingHTTPServer
        access_token = secrets.token_urlsafe(32)
        base_handler = server.handler(connector)
        class ProtectedHandler(base_handler):
            def authorized(self):
                if secrets.compare_digest(self.headers.get('X-HPC-Service-Token', ''), access_token):
                    return True
                self.reply(403, {'error': 'HPC App credential required'})
                return False
            def do_GET(self):
                if self.authorized():
                    super().do_GET()
            def do_POST(self):
                if self.authorized():
                    super().do_POST()
        http = ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), ProtectedHandler)
        private_json(root / '.hpc-app.json', dict(metadata, engine=config['engine'],
                     config_revision=result['config_revision'], rpc_token=connector.rpc_token, access_token=access_token))
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        while child.poll() is None and not stop.wait(.5):
            pass
        if child.poll() is not None and not stop.is_set():
            raise RuntimeError('Model engine exited; the Slurm App has ended')
    finally:
        if http:
            http.shutdown()
            http.server_close()
        terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
