"""Small App packages for native Fleet gates."""
import json


def consumer_package(path):
    path.mkdir()
    (path / 'server.py').write_text('''
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'ready')
ThreadingHTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
''')
    manifest = {'apiVersion': 2, 'id': 'model-consumer', 'version': '1.0.0', 'runtime': 'process',
                'execution': {'protocol': 1, 'manifest': 'fleet.json'}}
    definition = {'protocol': 1, 'app_id': 'model-consumer', 'version': '1.0.0',
        'components': [{'name': 'backend', 'runtime': 'process',
            'argv': ['python3', '${PACKAGE}/server.py'], 'ports': {'http': 0},
            'readiness': {'argv': ['python3', '-c',
                "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP'],timeout=1).read()"],
                'timeout_seconds': 10}}]}
    for name, value in (('app.json', manifest), ('fleet.json', definition)):
        (path / name).write_text(json.dumps(value))
