"""Real TLS, full-mesh monitoring and caller-owned CPU process cleanup."""
from contextlib import contextmanager, ExitStack
import socket
import subprocess
import sys
import threading
import time

import pytest

from pantheon.models import group_mesh as mesh
from pantheon.models import group_network as network
from test_model_group_network import document, issue


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Condition did not become true within its deadline')


@pytest.fixture
def transport(tmp_path, monkeypatch):
    value = document()
    value['members'].append(dict(rank=2, node_id='n_third', generation=5, address='fd12::3', port=18400))
    topology = network.PeerTopology(value)
    contexts = issue(tmp_path, topology)
    listeners, endpoints, dials = {}, {}, []
    original = socket.create_connection
    for rank in range(3):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(3)
        listeners[rank] = listener
        endpoints[(f'fd12::{rank+1}', 18400)] = listener.getsockname()

    @contextmanager
    def listen(bound, rank):
        assert bound.fingerprint == topology.fingerprint
        try:
            yield listeners[rank]
        finally:
            listeners[rank].close()

    def connect(address, timeout):
        dials.append(address)
        return original(endpoints[address], timeout=timeout)

    monkeypatch.setattr(network, 'listen', listen)
    monkeypatch.setattr(network.socket, 'create_connection', connect)
    try:
        yield topology, contexts, dials
    finally:
        for listener in listeners.values():
            listener.close()


def enter_all(stack, transport):
    topology, contexts, _ = transport
    return [stack.enter_context(mesh.PeerMesh(topology, rank, *contexts[rank],
        startup_timeout=2, peer_timeout=.6, interval=.1)) for rank in range(3)]


def test_three_rank_mesh_requires_every_original_ready_and_closes_all_workers(transport):
    with ExitStack() as stack:
        cohort = enter_all(stack, transport)
        for member in cohort:
            member.wait_connected()
            member.transition('loading')
        for member in cohort[:2]:
            member.transition('ready')
        assert not any(member.check(require_ready=True) for member in cohort)
        cohort[2].transition('ready')
        eventually(lambda: all(member.check(require_ready=True) for member in cohort))
        assert len(transport[2]) == 3  # exactly one persistent connection per pair
        with pytest.raises(ValueError):
            cohort[0].transition('loading')
    assert all(not thread.is_alive() for member in cohort for thread in member._threads)
    with pytest.raises(RuntimeError, match='reused'):
        cohort[0].__enter__()


def test_rank_failure_propagates_and_caller_stops_only_owned_processes(transport):
    processes, watchers = [], []
    untouched = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        with ExitStack() as stack:
            cohort = enter_all(stack, transport)
            for member in cohort:
                member.wait_connected()
                member.transition('loading')
                process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
                processes.append(process)
                def supervise(current=member, owned=process):
                    assert current.failed.wait(3)
                    owned.terminate()
                    owned.wait(timeout=3)
                watcher = threading.Thread(target=supervise)
                watcher.start()
                watchers.append(watcher)
                member.transition('ready')
            eventually(lambda: all(member.check(require_ready=True) for member in cohort))
            cohort[1].fail('owned-engine-exited')
            eventually(lambda: all(member.failed.is_set() for member in cohort))
            for watcher in watchers:
                watcher.join(timeout=4)
                assert not watcher.is_alive()
            assert all(process.poll() is not None for process in processes)
            assert untouched.poll() is None
            with pytest.raises(mesh.CohortLost):
                cohort[0].check(require_ready=True)
            assert len(transport[2]) == 3  # failure never reconnects or replaces ranks
    finally:
        for process in [*processes, untouched]:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)


def test_peer_stall_expires_even_when_tcp_stays_open(transport, monkeypatch):
    original = mesh.PeerMesh._message
    def stalled(self, peer, nonce):
        if self.rank == 0 and self._state == 'ready':
            self._closed.wait(2)
        return original(self, peer, nonce)
    monkeypatch.setattr(mesh.PeerMesh, '_message', stalled)
    with ExitStack() as stack:
        cohort = enter_all(stack, transport)
        for member in cohort:
            member.wait_connected()
            member.transition('loading')
        cohort[0].transition('ready')
        eventually(lambda: all(member.failed.is_set() for member in cohort))


def test_wrong_rank_certificate_cannot_join_cohort(transport):
    topology, contexts, _ = transport
    contexts[0] = contexts[1]
    with ExitStack() as stack:
        cohort = enter_all(stack, transport)
        eventually(lambda: all(member.failed.is_set() for member in cohort))
        assert all(member._state == 'failed' for member in cohort)


def test_initial_listener_refusal_can_wait_but_never_bypasses_full_cohort(transport, monkeypatch):
    original, refused = network.socket.create_connection, set()
    def delayed(address, timeout):
        if address not in refused:
            refused.add(address)
            raise ConnectionRefusedError('Original peer listener not ready')
        return original(address, timeout=timeout)
    monkeypatch.setattr(network.socket, 'create_connection', delayed)
    with ExitStack() as stack:
        cohort = enter_all(stack, transport)
        for member in cohort:
            member.wait_connected()
        assert len(refused) == 2 and len(transport[2]) == 3


def test_readiness_lease_cannot_outlive_heartbeat(transport):
    topology, contexts, _ = transport
    member = mesh.PeerMesh(topology, 0, *contexts[0])
    member._state = 'ready'
    member._observed = {rank: ('ready', time.monotonic() - 10) for rank in (1, 2)}
    with pytest.raises(mesh.CohortLost, match='expired'):
        member.check(require_ready=True)
    assert member.failed.is_set()


def test_missing_original_rank_prevents_start(transport):
    topology, contexts, _ = transport
    with mesh.PeerMesh(topology, 2, *contexts[2], startup_timeout=.15, peer_timeout=.6, interval=.1) as member:
        with pytest.raises(mesh.CohortLost):
            member.transition('loading')
        with pytest.raises(mesh.CohortLost):
            member.wait_connected()


@pytest.mark.parametrize('change', ['nonce', 'sender', 'topology', 'extra', 'protocol', 'state'])
def test_heartbeat_replay_or_changed_identity_is_rejected(transport, change):
    topology, contexts, _ = transport
    member = mesh.PeerMesh(topology, 1, *contexts[1])
    value = dict(protocol=2, kind='health', topology=topology.fingerprint,
                 sender=0, receiver=1, nonce='a' * 64, state='loading')
    value[change] = dict(nonce='b' * 64, sender=True, topology='c' * 64,
                        extra='not allowed', protocol=True, state='unknown')[change]
    with pytest.raises(network.PeerMismatch):
        member._receive(value, 0, 'a' * 64)
    assert not member._observed
