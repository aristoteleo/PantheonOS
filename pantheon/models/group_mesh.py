"""Live, bounded peer health for one immutable model-group attempt.

One TLS connection per unordered rank pair, always lower -> higher. A broken
authenticated connection ends this attempt; there is no reconnect, replacement,
restart or Fleet mutation. The owner must withdraw readiness and stop its OWN
engine when failed is set. This channel does not encrypt engine collectives.
"""
import secrets
import socket
import threading
import time

try:
    from . import group_network as network
except ImportError:  # The same modules are copied into a standalone engine package.
    import group_network as network


class CohortLost(RuntimeError):
    pass


class PeerMesh:
    def __init__(self, topology, rank, server_context, client_context, *,
                 startup_timeout=120, peer_timeout=5, interval=1):
        topology.member(rank)
        network._check_context(server_context, peer_timeout, server=True)
        network._check_context(client_context, peer_timeout, server=False)
        if (type(startup_timeout) not in (int, float) or not 0 < startup_timeout <= 600
                or type(interval) not in (int, float) or not 0 < interval < peer_timeout / 2):
            raise ValueError('Bound startup and heartbeat deadlines')
        self.topology, self.rank = topology, rank
        self.server_context, self.client_context = server_context, client_context
        self.startup_timeout, self.peer_timeout, self.interval = startup_timeout, peer_timeout, interval
        self.peers = set(range(len(topology.document()['members']))) - {rank}
        self.failed, self._closed = threading.Event(), threading.Event()
        self._lock = threading.RLock()
        self._threads, self._sockets, self._incoming, self._observed = [], set(), set(), {}
        self._state, self._reason, self._entered = 'waiting', '', False

    def _spawn(self, function, *args):
        with self._lock:
            if not self._closed.is_set():
                worker = threading.Thread(target=self._worker, args=(function, args), daemon=True)
                self._threads.append(worker)
                worker.start()

    def _worker(self, function, args):
        try:
            function(*args)
        except Exception as error:
            if not self._closed.is_set():
                # Do not retain exception text/certificates/endpoints in diagnostics.
                self.fail(type(error).__name__)

    def __enter__(self):
        if self._entered:
            raise RuntimeError('A peer mesh cannot be reused for another attempt')
        self._entered = True
        self._deadline = time.monotonic() + self.startup_timeout
        self._listener_context = network.listen(self.topology, self.rank)
        self._listener = self._listener_context.__enter__()
        self._spawn(self._accept)
        for peer in sorted(self.peers):
            if peer > self.rank:
                self._spawn(self._dial, peer)
        return self

    def _track(self, connection):
        with self._lock:
            if self._closed.is_set():
                connection.close()
                raise CohortLost('Attempt is closed')
            self._sockets.add(connection)

    def _forget(self, connection):
        with self._lock:
            self._sockets.discard(connection)

    def _accept(self):
        self._listener.settimeout(.2)
        accepted = 0
        while accepted < self.rank and not self._closed.is_set():
            if time.monotonic() >= self._deadline:
                raise TimeoutError('Original peers did not connect')
            try:
                raw, _ = self._listener.accept()
            except socket.timeout:
                continue
            # At most N-1 inbound handlers. Invalid/duplicate handshakes fail the
            # attempt instead of allowing an unbounded anonymous accept loop.
            self._track(raw)
            accepted += 1
            self._spawn(self._serve, raw)

    def _serve(self, raw):
        connection = None
        try:
            connection = self.server_context.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
            self._track(connection)
            connection.settimeout(self.peer_timeout)
            connection.do_handshake()
            peer = self.topology.certificate_rank(connection.getpeercert())
            with self._lock:
                if peer >= self.rank or peer in self._incoming:
                    raise network.PeerMismatch('Unexpected or repeated incoming rank')
                self._incoming.add(peer)
            while not self._closed.is_set():
                deadline = time.monotonic() + self.peer_timeout
                message = network._read(connection, deadline)
                nonce = message.get('nonce')
                if not network._matches(nonce, r'[a-f0-9]{64}'):
                    raise network.PeerMismatch('Invalid heartbeat challenge')
                self._receive(message, peer, nonce)
                network._send(connection, self._message(peer, nonce), deadline)
        finally:
            if connection is not None:
                self._forget(connection)
                connection.close()
            self._forget(raw)
            raw.close()

    def _dial(self, peer):
        member = self.topology.member(peer)
        # Retry only an initially unavailable listener. Once a TCP/TLS connection
        # is accepted, any handshake/protocol/connection failure is terminal.
        while not self._closed.is_set():
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Original peer did not listen')
            try:
                raw = socket.create_connection((member['address'], member['port']),
                                               timeout=min(self.peer_timeout, remaining))
                break
            except (ConnectionRefusedError, TimeoutError):
                self._closed.wait(min(.1, remaining))
        else:
            return
        connection = None
        try:
            connection = self.client_context.wrap_socket(raw,
                server_hostname=self.topology.certificate_name(peer), do_handshake_on_connect=False)
            self._track(connection)
            connection.settimeout(self.peer_timeout)
            connection.do_handshake()
            if self.topology.certificate_rank(connection.getpeercert()) != peer:
                raise network.PeerMismatch('Unexpected outgoing rank')
            while not self._closed.is_set():
                deadline, nonce = time.monotonic() + self.peer_timeout, secrets.token_hex(32)
                network._send(connection, self._message(peer, nonce), deadline)
                self._receive(network._read(connection, deadline), peer, nonce)
                self._closed.wait(self.interval)
        finally:
            if connection is not None:
                self._forget(connection)
                connection.close()
            raw.close()

    def _message(self, peer, nonce):
        with self._lock:
            state = self._state
        return dict(protocol=2, kind='health', topology=self.topology.fingerprint,
                    sender=self.rank, receiver=peer, nonce=nonce, state=state)

    def _receive(self, message, peer, nonce):
        state = message.get('state')
        if state not in {'waiting', 'loading', 'ready', 'failed'}:
            raise network.PeerMismatch('Invalid peer health state')
        expected = dict(protocol=2, kind='health', topology=self.topology.fingerprint,
                        sender=peer, receiver=self.rank, nonce=nonce, state=state)
        network._check_message(message, expected)
        with self._lock:
            previous = self._observed.get(peer)
            order = {'waiting': 0, 'loading': 1, 'ready': 2}
            if state == 'failed' or (previous and order[state] < order[previous[0]]):
                raise CohortLost('Original peer is no longer ready')
            self._observed[peer] = (state, time.monotonic())

    def transition(self, state):
        with self._lock:
            if (self._state, state) not in {('waiting', 'loading'), ('loading', 'ready')}:
                raise ValueError('Health transitions are monotonic within an attempt')
            self.check()
            if set(self._observed) != self.peers:
                raise CohortLost('Authenticate every original peer before launch')
            self._state = state

    def check(self, *, require_ready=False):
        with self._lock:
            if self.failed.is_set() or self._closed.is_set():
                raise CohortLost('Original cohort is unavailable: ' + self._reason)
            now = time.monotonic()
            if any(now - seen > self.peer_timeout for _, seen in self._observed.values()):
                self.fail('stale-heartbeat')
                raise CohortLost('Original peer heartbeat expired')
            connected = set(self._observed) == self.peers
            return connected and (not require_ready or
                (self._state == 'ready' and all(state == 'ready' for state, _ in self._observed.values())))

    def wait_connected(self):
        while time.monotonic() < self._deadline:
            if self.check():
                return
            self.failed.wait(.02)
        self.fail('startup-deadline')
        raise CohortLost('Original cohort startup deadline')

    def fail(self, reason='owned-engine-failed'):
        with self._lock:
            if not self.failed.is_set():
                self._reason, self._state = reason, 'failed'
                self.failed.set()
        # Tear down live channels to notify all connected peers promptly. Closing
        # is deliberate and terminal, not evidence that their resources are free.
        self._disconnect()

    def _disconnect(self):
        self._closed.set()
        with self._lock:
            connections = list(self._sockets)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def close(self):
        self._disconnect()
        # Every blocking operation has a bounded socket timeout; shutdown wakes
        # live I/O. No polling/accept workers survive this context's cleanup.
        deadline = time.monotonic() + self.peer_timeout + 1
        for worker in list(self._threads):
            worker.join(max(0, deadline - time.monotonic()))
        self._listener_context.__exit__(None, None, None)
        if any(worker.is_alive() for worker in self._threads):
            raise RuntimeError('Peer monitor failed to stop')

    def __exit__(self, *args):
        self.close()
