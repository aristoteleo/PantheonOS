"""Binary media routes behind the existing instance-scoped Fleet gateway.

The connector listens on node loopback; Fleet validates the workload grant.
X-Model-Config additionally rejects stale service configurations. No arbitrary
local path or provider-returned URL is accepted by these routes.
"""
import json
import re
import sqlite3


PREFIX = '/media/artifacts'


def handle(handler, connector):
    path = handler.path
    if path != PREFIX and not path.startswith(PREFIX + '/'):
        return False
    if not connector.slots.acquire(blocking=False):
        handler.reply(429, {'error': 'Service transfer capacity is full'})
        return True
    registered = False
    try:
        with connector.lock:
            if not connector.config or handler.headers.get('X-Model-Config') != connector.revision:
                handler.reply(409, {'error': 'Service configuration changed'})
                return True
            if not connector.accepting:
                handler.reply(409, {'error': 'Service is not accepting media transfers'})
                return True
            connector.media_transfers += 1
            registered = True
        store = connector.media_store()
        match = re.fullmatch(re.escape(PREFIX) + r'/([a-f0-9]{32})(/content|/complete)?', path)
        artifact, suffix = match.groups() if match else (None, None)
        method = handler.command
        handler.connection.settimeout(30)
        if path == PREFIX and method == 'POST':
            size = int(handler.headers.get('Content-Length', '0'))
            if not 0 < size <= 2048 or handler.headers.get('Transfer-Encoding'):
                raise ValueError('Media declarations must be bounded JSON')
            body = json.loads(handler.rfile.read(size))
            if not isinstance(body, dict) or set(body) - {'request_key', 'kind', 'mime', 'size', 'sha256'}:
                raise ValueError('Invalid input media declaration')
            # Only a node-side model driver can declare generated outputs.
            handler.reply(201, store.create(**body, purpose='input'))
        elif artifact and not suffix and method == 'GET':
            handler.reply(200, store.get(artifact))
        elif artifact and not suffix and method == 'PUT':
            size = int(handler.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024 * 1024 or handler.headers.get('Transfer-Encoding'):
                raise ValueError('Binary media chunks must be at most 1 MiB')
            offset = int(handler.headers.get('Upload-Offset', '-1'))
            data = handler.rfile.read(size)
            if len(data) != size:
                raise ValueError('Incomplete binary media chunk')
            handler.reply(200, store.append(artifact, offset, data))
        elif artifact and suffix == '/complete' and method == 'POST':
            if handler.headers.get('Content-Length', '0') != '0' or handler.headers.get('Transfer-Encoding'):
                raise ValueError('Media completion has no request body')
            handler.reply(200, store.seal(artifact))
        elif artifact and suffix == '/content' and method == 'GET':
            requested = handler.headers.get('Range', '')
            selected = re.fullmatch(r'bytes=(\d+)-(\d+)', requested)
            if not selected:
                raise ValueError('Request one explicit byte range of at most 1 MiB')
            first, last = map(int, selected.groups())
            metadata, data = store.read(artifact, first, last - first + 1)
            if not data:
                handler.reply(416, {'error': 'Media range is outside its contents'})
                return True
            handler.send_response(206)
            handler.send_header('Content-Type', metadata['mime'])
            handler.send_header('Content-Length', str(len(data)))
            handler.send_header('Content-Range', f"bytes {first}-{first + len(data) - 1}/{metadata['size']}")
            handler.send_header('ETag', '"' + metadata['sha256'] + '"')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
            handler.send_header('Content-Disposition', 'attachment')
            handler.end_headers()
            handler.wfile.write(data)
        elif artifact and not suffix and method == 'DELETE':
            store.remove(artifact)
            handler.reply(200, {'removed': True})
        else:
            handler.reply(404, {'error': 'Unknown media operation'})
    except (ValueError, TypeError, KeyError):
        handler.reply(400, {'error': 'Invalid media request, state, checksum or storage budget'})
    except (OSError, sqlite3.Error):
        # No absolute node paths or credentials in a public error response.
        handler.reply(503, {'error': 'Media storage or transfer is unavailable'})
    finally:
        with connector.lock:
            if registered:
                connector.media_transfers -= 1
            connector.changed.notify_all()
        connector.slots.release()
    return True
