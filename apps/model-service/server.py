"""Fleet model connector. Stdlib only; engines and credentials stay on the node.

Attached engines remain externally owned. Explicit managed deployments also
offer separate authenticated preparation/model jobs. Inference grants cannot
configure services or manage engines, downloads or model memory.
"""
import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path, PureWindowsPath
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_config(value):
    if set(value) - {'engine', 'endpoint', 'credential_file'}:
        raise ValueError('Unsupported connector configuration')
    engine = value.get('engine')
    if engine not in {'ollama', 'lmstudio', 'sglang', 'api'}:
        raise ValueError('Choose Ollama, LM Studio, SGLang or an API endpoint')
    endpoint = str(value.get('endpoint', '')).rstrip('/')
    p = urlsplit(endpoint)
    if (p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password
            or p.query or p.fragment):
        raise ValueError('Use an HTTP(S) endpoint without credentials, query or fragment')
    local = p.hostname in {'localhost', '127.0.0.1', '::1'}
    if engine != 'api' and not local:
        raise ValueError('An attached local engine must use a loopback endpoint on the selected node')
    if p.scheme != 'https' and not local:
        raise ValueError('Remote API endpoints require HTTPS')
    credential = value.get('credential_file', '')
    if credential and (not isinstance(credential, str) or not
                       (Path(credential).is_absolute() or PureWindowsPath(credential).is_absolute())):
        raise ValueError('Credential file must be an absolute path on the selected node')
    # The API prefix is part of the endpoint, e.g. localhost:1234/v1.
    if not p.path.rstrip('/'):
        endpoint += '/v1'
    return {'engine': engine, 'endpoint': endpoint, 'credential_file': credential}


class Connector:
    def __init__(self, data):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / 'connector.json'
        self.lock = threading.RLock()
        self.calls = {}
        self.maintenance = False
        self.accepting = True
        self.control = secrets.token_urlsafe(32)
        # A model inference grant is not permission to configure an endpoint,
        # download weights or load/unload models. Only Fleet owner RPC carries
        # this generation-bound node-local credential; it is never proxied on.
        self.rpc_token = os.environ.get('PANTHEON_APP_RPC_TOKEN') or secrets.token_urlsafe(32)
        self.run_id = secrets.token_hex(16)
        self.slots = threading.BoundedSemaphore(16)
        self.config = json.loads(self.path.read_text()) if self.path.exists() else None
        self._downloads = None
        self._engine_downloads = None
        self._model_control = None
        self._snapshots = None
        self._modules = {}
        self._probe_lock = threading.Lock()
        self._probe = None

    def module(self, name):
        with self.lock:
            if name not in self._modules:
                from importlib.util import spec_from_file_location, module_from_spec
                spec = spec_from_file_location('fleet_model_' + name, Path(__file__).with_name(name + '.py'))
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                self._modules[name] = module
            return self._modules[name]

    def engine_downloads(self):
        with self.lock:
            if self._engine_downloads is None:
                downloads = self.downloads()
                artifacts, engines = self.module('artifacts'), self.module('engines')
                root = downloads.cache.root.parent
                scope = os.environ.get('PANTHEON_APP_SCOPE', '')
                engine_scope = 'engine-' + scope.removeprefix('model-')
                self._engine_downloads = artifacts.DownloadJobs(downloads.directory / 'engines',
                    engines.EngineCache(root, downloads.cache, artifacts.file_lock, artifacts.atomic_json, engine_scope))
            return self._engine_downloads

    def engine_catalog(self):
        engines = self.module('engines')
        root = Path(os.environ.get('PANTHEON_APP_CACHE') or self.data / 'cache')
        scope = 'engine-' + os.environ.get('PANTHEON_APP_SCOPE', '').removeprefix('model-')
        return {'recipes': [{**r, 'prepared': bool(engines.prepared(root, r, scope, verify_files=False)),
                             'unavailable_reason': engines.requirement(r)} for r in engines.catalog()
                            if engines.native_platform() in r['platforms']]}

    def prepare_engine(self, recipe_id, resume=False):
        engines = self.module('engines')
        selected = engines.recipe(recipe_id)
        if selected.get('runtime') == 'container':
            raise ValueError('Fleet prepares the pinned container when the service starts')
        if error := engines.requirement(selected):
            raise ValueError(error)
        return {'job_id': self.engine_downloads().submit(recipe_id, selected['source'], resume=resume)}

    def snapshot_jobs(self):
        with self.lock:
            if self._snapshots is None:
                downloads, artifacts = self.downloads(), self.module('artifacts')
                self._snapshots = artifacts.DownloadJobs(downloads.directory / 'snapshots',
                    self.module('snapshots').SnapshotCache(downloads.cache.root.parent, artifacts))
            return self._snapshots

    def prepare_snapshot(self, artifact_job_id, resume=False):
        downloads = self.downloads()
        with downloads.mutex:
            row = downloads.db.execute('SELECT source,state FROM jobs WHERE id=?', (artifact_job_id,)).fetchone()
        if not row or row[1] != 'ready':
            raise ValueError('Choose a verified model download')
        source = json.loads(row[0])
        if source['format'] != 'safetensors.tar.gz':
            raise ValueError('SGLang requires a safetensors.tar.gz model bundle')
        return {'job_id': self.snapshot_jobs().submit(source['sha256'], source, resume=resume)}

    def downloads(self):
        with self.lock:
            if self._downloads is None:
                # Load by package path for both immutable Fleet artifacts and
                # embedded test/import contexts; no third-party dependency.
                module = self.module('artifacts')
                root = Path(os.environ.get('PANTHEON_APP_CACHE') or self.data / 'cache')
                scope = os.environ.get('PANTHEON_APP_SCOPE', 'app')
                import re
                if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}', scope):
                    raise ValueError('Invalid deployment scope')
                self._downloads = module.DownloadJobs(root / 'tasks' / scope, module.ArtifactCache(root / 'blobs'))
            return self._downloads

    @property
    def revision(self):
        return hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()

    def model_control(self):
        with self.lock:
            if not (self.config or {}).get('managed'):
                raise ValueError('Model management is only enabled for an owned engine')
            if self._model_control is None:
                self._model_control = self.module('model_control').ModelControl(self)
            return self._model_control

    def configure(self, config, managed=None):
        config = validate_config(config)
        if managed is not None:
            config['managed'] = self.module('model_control').management_config(managed,
                config['engine'], os.environ.get('PANTHEON_APP_SCOPE', ''), self.module('engines'))
        with self.lock:
            if self.calls or self.maintenance:
                raise ValueError('Wait for active requests before changing the service')
            tmp = self.path.with_suffix('.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(config, f)
            os.replace(tmp, self.path)
            self.config = config
            return {'config_revision': self.revision}

    def request(self, path, payload=None, *, config=None, timeout=120):
        config = self.config if config is None else config
        if not config:
            raise ValueError('Configure the connector first')
        headers = {'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
        file = config.get('credential_file')
        if file:
            # A reference, never a key copied into the App artifact or registry.
            with open(file, 'r') as stream:
                key = stream.read(8193).strip()
            if not key or len(key) > 8192 or '\n' in key or '\r' in key:
                raise ValueError('Invalid node credential file')
            headers['Authorization'] = 'Bearer ' + key
        req = Request(config['endpoint'] + path, headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None)
        return build_opener(NoRedirect).open(req, timeout=timeout)

    def discover(self):
        with self.lock:
            revision = self.revision
            config = dict(self.config or {})
        if config.get('managed'):
            models = self.model_control().status()['models']
            return {'models': [{'id': m['id']} for m in models], 'config_revision': revision}
        # A slow metadata endpoint must not hold the cancellation/admission lock.
        with self.request('/models', config=config, timeout=10) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('Model catalog is too large')
        rows = json.loads(body).get('data', [])
        # Do not guess capabilities from model names. Publish confirmed
        # capabilities separately; preserve unknowns in the discovery UI.
        models = [{'id': row['id']} for row in rows if isinstance(row, dict)
                  and isinstance(row.get('id'), str) and 0 < len(row['id']) <= 200]
        return {'models': models[:1000], 'config_revision': revision}

    def cancel(self, request_id):
        with self.lock:
            call = self.calls.get(request_id)
            if call:
                call['cancelled'] = True
                upstream = call.get('upstream')
                if upstream:
                    # Unblock a read immediately, including quiet generations.
                    try:
                        upstream.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                    except (AttributeError, OSError):
                        pass
            return {'cancelled': bool(call)}

    def route_state(self):
        """Read-only bounded preflight; no model loads and no inference requests.

        A two-second cache avoids repeated engine metadata scans for concurrent
        callers. Actual admission still rechecks drain, capacity and config.
        """
        with self._probe_lock:
            revision = self.revision
            if not self._probe or self._probe[0] != revision or self._probe[1] < time.monotonic():
                if (self.config or {}).get('managed'):
                    models = [{'id': m['id'], 'loaded': m['loaded']} for m in self.model_control().status()['models']]
                else:
                    models = [{**m, 'loaded': None} for m in self.discover()['models']]
                self._probe = (revision, time.monotonic() + 2, models)
            models = self._probe[2]
        with self.lock:
            return {'protocol': 1, 'config_revision': revision, 'models': models,
                    'ready': bool(self.config) and self.accepting and not self.maintenance,
                    'active_calls': len(self.calls),
                    'capacity': (self.config or {}).get('managed', {}).get('parallel', 4)}

    def drain(self):
        with self.lock:
            self.accepting = False
            if self.calls or self.maintenance:
                return {'status': 'waiting', 'safe_to_stop': False,
                        'message': 'Model calls or model operations are still active'}
            return {'status': 'succeeded', 'safe_to_stop': True}


def handler(connector):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No prompts, credentials or upstream errors in process logs.

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/route-state':
                if self.headers.get('X-Model-Config') != connector.revision:
                    return self.reply(409, {'error': 'Service configuration changed'})
                try:
                    return self.reply(200, connector.route_state())
                except (ValueError, OSError, HTTPException):
                    return self.reply(503, {'error': 'The selected engine is unavailable'})
            if self.path == '/health':
                with connector.lock:
                    return self.reply(200, {'ready': True, 'active_calls': len(connector.calls),
                                            'configured': bool(connector.config), 'run_id': connector.run_id})
            self.reply(404, {'error': 'Unknown endpoint'})

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2 * 1024 * 1024:
                    return self.reply(413, {'error': 'Request must be at most 2 MiB'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
                if self.path == '/rpc':
                    if not secrets.compare_digest(self.headers.get('X-Fleet-RPC-Token', ''), connector.rpc_token):
                        return self.reply(403, {'error': 'Fleet management RPC credential required; update Fleet if this is a management call'})
                    method, args = body.get('method'), body.get('args', {})
                    if method == 'configure':
                        result = connector.configure(**args)
                    elif method == 'discover':
                        result = connector.discover()
                    elif method == 'status':
                        result = {'active_calls': len(connector.calls), 'active_model_operations': int(connector.maintenance),
                                  'config_revision': connector.revision}
                    elif method == 'drain':
                        result = connector.drain()
                    elif method == 'models_status':
                        result = connector.model_control().status()
                    elif method == 'models_submit':
                        result = connector.model_control().submit(**args)
                    elif method == 'models_forget':
                        connector.model_control().forget(**args)
                        result = {'ok': True}
                    elif method == 'artifacts_list':
                        result = {'jobs': connector.downloads().list()}
                    elif method == 'snapshots_prepare':
                        result = connector.prepare_snapshot(**args)
                    elif method == 'snapshots_jobs':
                        result = {'jobs': connector.snapshot_jobs().list()}
                    elif method == 'snapshots_cancel':
                        result = {'cancelled': connector.snapshot_jobs().cancel(**args)}
                    elif method == 'snapshots_status':
                        record = connector.module('snapshots').snapshot(connector.downloads().cache.root.parent, args['sha256'])
                        result = {'ready': bool(record), 'estimate': connector.module('snapshots').memory_estimate(record,
                                  args['context_length'], args['parallel']) if record else None}
                    elif method == 'artifacts_submit':
                        result = {'job_id': connector.downloads().submit(**args)}
                    elif method == 'artifacts_cancel':
                        result = {'cancelled': connector.downloads().cancel(**args)}
                    elif method == 'artifacts_forget':
                        connector.downloads().forget(**args)
                        result = {'ok': True}
                    elif method == 'engines_catalog':
                        result = connector.engine_catalog()
                    elif method == 'engines_prepare':
                        result = connector.prepare_engine(**args)
                    elif method == 'engines_jobs':
                        result = {'jobs': connector.engine_downloads().list()}
                    elif method == 'engines_cancel':
                        result = {'cancelled': connector.engine_downloads().cancel(**args)}
                    else:
                        raise ValueError('Unknown method')
                    return self.reply(200, result)
                if self.path == '/cancel':
                    return self.reply(200, connector.cancel(body.get('request_id')))
                if self.path == '/drain':
                    if not secrets.compare_digest(self.headers.get('X-Control-Key', ''), connector.control):
                        return self.reply(403, {'error': 'Lifecycle control credential required'})
                    return self.reply(200, connector.drain())
                if self.path not in {'/v1/chat/completions', '/v1/embeddings'}:
                    return self.reply(404, {'error': 'Unsupported model operation'})
                self.proxy(body)
            except (ValueError, TypeError):
                self.reply(400, {'error': 'Invalid connector request or configuration'})
            except (HTTPError, OSError):
                self.reply(502, {'error': 'Cannot reach the configured model endpoint or credential file on this node'})

        def proxy(self, body):
            request_id = self.headers.get('X-Model-Request', '')
            if not request_id or len(request_id) > 100:
                return self.reply(400, {'error': 'A request id is required'})
            if not connector.slots.acquire(blocking=False):
                return self.reply(429, {'error': 'This service is at its active request limit'})
            call = {'cancelled': False, 'model': body.get('model')}
            begun = False
            try:
                with connector.lock:
                    if not connector.accepting:
                        return self.reply(503, {'error': 'Model connector is stopping'})
                    if connector.maintenance:
                        return self.reply(503, {'error': 'An owned model operation is in progress'})
                    if self.headers.get('X-Model-Config') != connector.revision:
                        return self.reply(409, {'error': 'Service configuration changed; refresh the model catalog'})
                    if request_id in connector.calls:
                        return self.reply(409, {'error': 'Request already running'})
                    managed = (connector.config or {}).get('managed')
                    if len(connector.calls) >= (managed['parallel'] if managed else 4):
                        return self.reply(429, {'error': 'This service is at its configured concurrency limit'})
                    if managed and any(c.get('model') != body.get('model') for c in connector.calls.values()):
                        return self.reply(429, {'error': 'Wait for the current model’s active requests before switching models'})
                    connector.calls[request_id] = call
                if managed:
                    # Admission above fences load/unload/configure while we
                    # validate identity. Never hold the cancellation lock while
                    # waiting on an engine metadata request.
                    connector.model_control().inference_model(body)
                try:
                    upstream = connector.request(self.path.removeprefix('/v1'), body)
                except HTTPError as error:
                    # Providers sometimes echo keys or prompts in error bodies.
                    return self.reply(error.code, {'error': f'Model endpoint rejected the request (HTTP {error.code})'})
                with upstream:
                    with connector.lock:
                        call['upstream'] = upstream
                    if call['cancelled']:
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', upstream.headers.get('Content-Type', 'application/json'))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Accel-Buffering', 'no')
                    self.end_headers()
                    begun = True
                    deadline, total = time.monotonic() + 600, 0
                    while not call['cancelled'] and time.monotonic() < deadline:
                        block = upstream.read1(16384)
                        if not block:
                            break
                        total += len(block)
                        if total > 64 * 1024 * 1024:
                            break
                        self.wfile.write(block)
                        self.wfile.flush()
            except (OSError, HTTPException):
                if not begun:
                    self.reply(502, {'error': 'Model endpoint unavailable on the selected node'})
            finally:
                with connector.lock:
                    if connector.calls.get(request_id) is call:
                        connector.calls.pop(request_id)
                connector.slots.release()
    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['start', 'ready', 'drain'])
    parser.add_argument('--data', default='.')
    args = parser.parse_args()
    endpoint_file = Path(args.data) / 'endpoint.json'
    if args.command != 'start':
        endpoint = json.loads(endpoint_file.read_text())
        port = endpoint['port']
    if args.command == 'ready':
        with build_opener().open(f'http://127.0.0.1:{port}/health', timeout=3) as response:
            assert response.status == 200 and json.load(response)['run_id'] == endpoint['run_id']
    elif args.command == 'drain':
        req = Request(f'http://127.0.0.1:{port}/drain', data=b'{}', headers={
            'Content-Type': 'application/json', 'X-Control-Key': endpoint['control']})
        with build_opener().open(req, timeout=3) as response:
            print(response.read().decode())
    else:
        connector = Connector(args.data)
        server = ThreadingHTTPServer(('127.0.0.1', int(os.environ['PANTHEON_PORT_HTTP'])), handler(connector))
        fd = os.open(endpoint_file, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'port': server.server_port, 'run_id': connector.run_id, 'control': connector.control}, stream)
        server.serve_forever()
