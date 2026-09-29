import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from pantheon.apps.builtin.fleet.hpc_apps import build


def test_app_specs_validate_origins_modules_and_bound_payloads():
    lab = build('jupyterlab', 'lab', atrium_origin='http://localhost:5173', modules=['devel', 'py-jupyterlab/4.3.2_py312'])
    assert lab['kind'] == 'jupyterlab' and lab['argv'][:2] == ['bash', '-lc']
    assert 'exec "$@"' in lab['argv'][2]
    models = build('model-service', 'models', engine_argv=['ollama', 'serve'])
    assert len(json.dumps(models).encode()) < 65536
    assert 'pip install' not in json.dumps(models)
    for values in ({'atrium_origin': 'https://example.test/path'}, {'atrium_origin': 'http://remote.test'},
                   {'atrium_origin': 'https://example.test', 'modules': ['devel;exit']},
                   {'atrium_origin': 'https://example.test', 'startup_seconds': True}):
        with pytest.raises(ValueError):
            build('jupyterlab', 'lab', **values)


def test_job_connector_uses_existing_protocol_authentication_and_cleanup(tmp_path):
    # A real child HTTP process provides an OpenAI-compatible fixture. This
    # tests the deployed bundle, not a mock of the connector or its transport.
    engine = '''import json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{"data":[{"id":"fixture-model"}]}')
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  data=json.dumps({'choices':[{'message':{'role':'assistant','content':'HPC_OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
open(os.environ['ENGINE_PID_FILE'],'w').write(str(os.getpid()))
HTTPServer(('127.0.0.1',int(os.environ['OLLAMA_HOST'].rsplit(':',1)[1])),Handler).serve_forever()
'''
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    spec = build('model-service', 'models', python=sys.executable, engine_argv=[sys.executable, '-c', engine],
                 model_cache=str(tmp_path / 'cache'), startup_seconds=15)
    pid_file = tmp_path / 'engine.pid'
    log = tmp_path / 'output'
    with log.open('w') as output:
        proc = subprocess.Popen(spec['argv'], cwd=tmp_path, stdout=output, stderr=output, env={**os.environ,
            'SLURM_JOB_ID': '1234', 'PORT': str(port), 'PANTHEON_HPC_WORKSPACE': str(tmp_path),
            'PANTHEON_HPC_SERVICE_REVISION': 'a' * 64, 'ENGINE_PID_FILE': str(pid_file)})
        try:
            path = tmp_path / '.hpc-app.json'
            deadline = time.monotonic() + 20
            while not path.exists():
                assert proc.poll() is None, log.read_text()
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(.1)
            access = json.loads(path.read_text())
            assert path.stat().st_mode & 0o777 == 0o600
            headers = {'X-HPC-Service-Token': access['access_token'], 'X-Model-Config': access['config_revision'],
                       'X-Model-Request': '1234567890abcdef1234567890abcdef'}
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=10) as http:
                assert http.get('/health').status_code == 403
                assert http.post('/rpc', headers=headers, json={'method': 'configure', 'args': {}}).status_code == 403
                discovery = http.post('/rpc', headers={**headers, 'X-Fleet-RPC-Token': access['rpc_token']}, json={'method': 'discover'}).json()
                assert discovery['models'] == [{'id': 'fixture-model'}]
                response = http.post('/v1/chat/completions', headers=headers, json={'model': 'fixture-model', 'messages': [{'role': 'user', 'content': 'test'}], 'stream': False})
                assert response.status_code == 200, response.text
                assert response.json()['choices'][0]['message']['content'] == 'HPC_OK'
                activity = http.post('/rpc', headers={**headers, 'X-Fleet-RPC-Token': access['rpc_token']}, json={'method': 'activity'}).json()
                assert 'fixture-model' in json.dumps(activity)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        engine_pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(engine_pid, 0)
        assert access['access_token'] not in log.read_text()
        assert access['rpc_token'] not in log.read_text()
