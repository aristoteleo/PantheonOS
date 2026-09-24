"""Rank-zero connector for one immutable, supervised model cohort.

Reuse the ordinary connector's FIFO, stream, cancellation and bounded activity
history. Only the Fleet owner may activate or drain this generation; consumers
cannot change endpoints, load weights or manage other ranks. The coordinator
must publish this binding and drain it before stopping ANY healthy rank.
"""
import hmac
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading

import server as service
import sglang_group
from group_inference import configuration


class GroupConnector(service.Connector):
    def __init__(self, data, run, plan, record, identity, token, engine_port=30000):
        if (not isinstance(identity, dict) or set(identity) != {'instance_id', 'generation'}
                or not isinstance(identity['instance_id'], str)
                or re.fullmatch('[a-f0-9]{32}', identity['instance_id']) is None
                or type(identity['generation']) is not int
                or not isinstance(token, str) or not 16 <= len(token) <= 512):
            raise ValueError('Missing original Fleet leader identity or credential')
        topology = sglang_group._validate(plan, record)[3]
        if identity['generation'] != topology.member(0)['generation']:
            raise ValueError('Leader generation differs from the pinned cohort')
        data = Path(data) / (identity['instance_id'] + '-' + str(identity['generation']))
        # This is not an attached/managed connector's mutable configuration or
        # media job directory. Never restore jobs belonging to another mode.
        if (data / 'connector.json').exists() or (data / 'media').exists():
            raise ValueError('Group state cannot contain mutable connector configuration or media jobs')
        super().__init__(data)
        self.run, self.identity = run, dict(identity)
        # The pinned configuration (and its revision) names the container port;
        # a process rank's engine listens on its Fleet-reserved port instead.
        self.engine_port = engine_port
        self.rpc_token = token
        self.model = 'fleet-snapshot-' + record['sha256']
        self.config = configuration(topology.document(), identity, plan['context_length'], plan['parallel'])
        self.enabled = plan.get('inference_protocol') == 1
        self.accepting = False
        self.fence = data / 'group-admission.json'
        self.drained = False
        if self.fence.exists():
            if self.fence.stat().st_size > 4096:
                raise ValueError('Invalid group admission fence')
            saved = json.loads(self.fence.read_text())
            if saved != dict(protocol=1, config_revision=self.revision, phase='drained'):
                raise ValueError('Group admission fence belongs to a different original deployment')
            self.drained = True

    @property
    def capacity(self):
        return self.config['parallel']

    def configure(self, *args, **kwargs):
        raise ValueError('Group configuration is immutable')

    def ready(self):
        return self.accepting and not self.drained and self.run.ready()

    def admission_error(self, call):
        if call['model'] != self.model:
            call['reason'] = 'different_group_model'
            return 400, 'Use the model pinned to this group'
        if not self.ready():
            call['reason'] = 'cohort_not_ready'
            return 503, 'The original model cohort is not ready'

    def inference_request(self, path, payload, call, **kwargs):
        # Queue admission is not proof of readiness at submission time.
        if self.admission_error(call):
            raise OSError('Original model cohort is unavailable')
        original = self.run.engine.listener_identity(self.engine_port)
        if original is None:
            raise OSError('Original model listener is unavailable')
        response = super().inference_request(path, payload, call, **kwargs)
        if self.run.engine.listener_identity(self.engine_port) != original:
            response.close()
            raise OSError('Original model listener changed')
        return response

    def request_spec(self, path, payload=None, **kwargs):
        req = super().request_spec(path, payload, **kwargs)
        if self.engine_port != 30000:
            from urllib.parse import urlsplit
            url = urlsplit(req.full_url)
            req.full_url = url._replace(netloc=f'{url.hostname}:{self.engine_port}').geturl()
        return req

    def discover(self):
        if not self.run.ready() or self.drained:
            raise ValueError('The original model cohort is not ready')
        return dict(models=[dict(id=self.model, operations=['text'])], config_revision=self.revision)

    def route_state(self):
        with self.lock:
            ready = self.ready()
            return dict(protocol=1, config_revision=self.revision, ready=ready,
                models=[dict(id=self.model, loaded=ready, inference_ready=ready)],
                active_calls=len(self.running_calls()), queued_calls=len(self.queue),
                queue_capacity=self.queue_capacity, capacity=self.capacity)

    def activity_status(self):
        with self.lock:
            return {**super().activity_status(), 'accepting': self.ready()}

    def resume(self, config_revision):
        with self.changed:
            if not self.enabled or config_revision != self.revision or self.drained or not self.run.ready():
                raise ValueError('Only the ready original undrained cohort can be activated')
            self.accepting = True
            self.changed.notify_all()
            return dict(config_revision=self.revision, accepting=True)

    def drain(self):
        with self.changed:
            # Persist before acknowledging. A repeated call or process restart
            # must never reopen admission after the owner began group shutdown.
            self.accepting = False
            self.drained = True
            self.module('idle').atomic_json(self.fence,
                dict(protocol=1, config_revision=self.revision, phase='drained'))
            return super().drain()


def handler(connector):
    class Handler(service.handler(connector)):
        def authorized(self, owner=False):
            header = 'X-Fleet-RPC-Token' if owner else 'X-Pantheon-App-Token'
            if not hmac.compare_digest(self.headers.get(header, '').encode(), connector.rpc_token.encode()):
                self.reply(403 if owner else 401, {'error': 'Unauthorized'})
                return False
            return True

        # Consumer inference reaches this loopback listener only through Fleet
        # (relay gateway or direct peer), which already verified the workload
        # grant; like the ordinary connector, it carries no node RPC token.
        # Owner control (/rpc) and Fleet readiness (/ready) keep their tokens.
        def do_GET(self):
            if self.path == '/ready' and not self.authorized():
                return
            if self.path == '/ready':
                # Fleet readiness precedes owner publication/activation.
                ready = connector.run.ready()
                return self.reply(200 if ready else 503, dict(protocol=1, ready=ready, **connector.identity))
            if self.path == '/route-state':
                if self.headers.get('X-Model-Config') != connector.revision:
                    return self.reply(409, {'error': 'Service configuration changed'})
                return self.reply(200, connector.route_state())
            self.reply(404, {'error': 'Unknown group endpoint'})

        def do_POST(self):
            if self.path == '/rpc' and not self.authorized(owner=True):
                return
            if self.path not in {'/rpc', '/cancel', '/v1/chat/completions'}:
                return self.reply(404, {'error': 'Unsupported group operation'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < size <= 2 * 1024 * 1024:
                    return self.reply(413, {'error': 'Use a bounded JSON request of at most 2 MiB'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Expected an object')
                if self.path == '/rpc':
                    methods = dict(discover=connector.discover, activity=connector.activity_status,
                        status=lambda: dict(**connector.route_state(), **connector.identity,
                            active_model_operations=0, accepting=connector.ready(), recovery_protocol=1),
                        resume=connector.resume, drain=connector.drain, cancel_request=connector.cancel)
                    args = body.get('args', {})
                    if body.get('method') not in methods or not isinstance(args, dict):
                        raise ValueError('Unsupported owner operation')
                    return self.reply(200, methods[body['method']](**args))
                if self.path == '/cancel':
                    if self.headers.get('X-Model-Config') != connector.revision:
                        return self.reply(409, {'error': 'Service configuration changed'})
                    return self.reply(200, connector.cancel(body.get('request_id')))
                return self.proxy(body)
            except (ValueError, TypeError):
                self.reply(400, {'error': 'Invalid group request or unavailable original cohort'})
            except OSError:
                self.reply(503, {'error': 'Group request could not be completed'})

        def do_PUT(self):
            self.reply(404, {'error': 'Unsupported group operation'})

        do_DELETE = do_PUT
    return Handler


class GroupServer(ThreadingHTTPServer):
    """Bound sockets/threads including unauthenticated or slow header clients."""
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, request_handler):
        self.connections = threading.BoundedSemaphore(64)
        super().__init__(address, request_handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


def serve(connector, port):
    server = GroupServer(('127.0.0.1', port), handler(connector))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
    thread.start()
    return server, thread
