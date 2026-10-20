"""The single-machine model directory, served the way the Hub serves it.

On a cloud Fleet a Connector registers itself in the owner's Hub model
directory (``/api/model-services``). A single-machine Fleet has no Hub: this
loopback endpoint serves the same API over the profile's file directory
(``LocalModelDirectory``), which Model management and Model access read
directly (``directory_root``). Only bearers of this process's key may call it.
"""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from pathlib import Path
import threading

from pantheonos.appkit.dependency_assembly import AssemblyError
from pantheonos.local.launch import PrivateJournal

from pantheonos.models.local_directory import LocalModelDirectory
from pantheonos.models.errors import ControlError

PREFIX = '/api/model-services'
MAX_BODY = 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def _reply(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _serve(self, method):
        server = self.server
        token = self.headers.get('Authorization', '')
        if not hmac.compare_digest(token.encode(), ('Bearer ' + server.key).encode()):
            return self._reply(401, {'detail': 'unauthorized'})
        path = self.path.split('?', 1)[0].rstrip('/')
        if path != PREFIX and not path.startswith(PREFIX + '/'):
            return self._reply(404, {'detail': 'not found'})
        data = None
        if method in ('PUT', 'POST', 'DELETE'):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError
                data = json.loads(self.rfile.read(size))
            except ValueError:
                return self._reply(400, {'detail': 'invalid body'})
        try:
            result = server.directory._request(method, path, data, method in ('PUT', 'DELETE'))
        except ControlError as error:
            return self._reply(error.status, {'detail': str(error)})
        except (ValueError, KeyError, TypeError):
            return self._reply(422, {'detail': 'invalid model directory request'})
        return self._reply(200, result)

    def do_GET(self):
        self._serve('GET')

    def do_PUT(self):
        self._serve('PUT')

    def do_POST(self):
        self._serve('POST')

    def do_DELETE(self):
        self._serve('DELETE')


class DirectoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, directory: LocalModelDirectory, *, state=None):
        """state: a private file keeping the port and key across launches, so
        the Connector's directory credential stays the same."""
        saved = {}
        if state is not None:
            try:
                saved = PrivateJournal(Path(state).parent).read(state)
            except (AssemblyError, OSError):
                saved = {}
        self.directory = directory
        self.key = saved.get('key') if isinstance(saved.get('key'), str) else secrets.token_urlsafe(32)
        try:
            super().__init__(('127.0.0.1', int(saved.get('port', 0))), _Handler)
        except (OSError, ValueError, TypeError):
            super().__init__(('127.0.0.1', 0), _Handler)
        if state is not None and saved != {'port': self.server_port, 'key': self.key}:
            PrivateJournal(Path(state).parent)._write(Path(state), {'port': self.server_port, 'key': self.key})
        self.endpoint = f'http://127.0.0.1:{self.server_port}{PREFIX}'
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self.serve_forever, kwargs={'poll_interval': .2}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.shutdown()
        self.server_close()
        self._thread.join()
