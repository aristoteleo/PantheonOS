"""Small typed job control messages on the instance's authenticated HTTP plane."""
import json
import re
import sqlite3


PREFIX = '/inference/jobs'


def handle(handler, connector):
    path = handler.path
    if path != PREFIX and not path.startswith(PREFIX + '/'):
        return False
    try:
        with connector.lock:
            if not connector.config or handler.headers.get('X-Model-Config') != connector.revision:
                handler.reply(409, {'error': 'Service configuration changed'})
                return True
        handler.connection.settimeout(30)
        match = re.fullmatch(re.escape(PREFIX) + r'/([A-Za-z0-9_-]{1,100})(/cancel|/reconcile)?', path)
        job, suffix = match.groups() if match else (None, None)
        method = handler.command
        if handler.headers.get('Transfer-Encoding'):
            raise ValueError('Typed jobs require a bounded request body')
        length = int(handler.headers.get('Content-Length', '0'))
        if path == PREFIX and method == 'POST':
            if not 0 < length <= 128 * 1024:
                raise ValueError('Invalid typed job size')
            raw = handler.rfile.read(length)
            if len(raw) != length:
                raise ValueError('Incomplete typed job body')
            body = json.loads(raw)
            receipt = connector.inference_jobs().submit(body, handler.headers['X-Model-Config'])
            handler.reply(202, receipt)
        elif length:
            raise ValueError('This job operation has no body')
        elif path == PREFIX and method == 'GET':
            handler.reply(200, {'protocol': 1, 'jobs': connector.inference_jobs().list()})
        elif job and not suffix and method == 'GET':
            prefer = handler.headers.get('Prefer', 'wait=0')
            if not re.fullmatch(r'wait=[0-5]', prefer):
                raise ValueError('Invalid status wait preference')
            record = connector.inference_jobs().status(job, wait_seconds=int(prefer[-1]))
            with connector.lock:
                stale = handler.headers.get('X-Model-Config') != connector.revision
            # Never hold the lifecycle/cancellation lock during socket writes.
            if stale:
                handler.reply(409, {'error': 'Service configuration changed'})
            else:
                handler.reply(200, record)
        elif job and suffix == '/cancel' and method == 'POST':
            handler.reply(200, connector.inference_jobs().cancel(job))
        elif job and suffix == '/reconcile' and method == 'POST':
            handler.reply(200, connector.inference_jobs().reconcile(job))
        elif job and not suffix and method == 'DELETE':
            connector.inference_jobs().remove(job)
            handler.reply(200, {'removed': True})
        else:
            handler.reply(404, {'error': 'Unknown inference job operation'})
    except KeyError:
        handler.reply(404, {'error': 'Inference job not found'})
    except (ValueError, TypeError):
        handler.reply(409, {'error': 'Invalid job, changed identity, unsupported operation or unavailable capacity'})
    except (OSError, sqlite3.Error):
        handler.reply(503, {'error': 'Inference job storage or transport is unavailable'})
    return True
