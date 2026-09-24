"""Actual mTLS meshes and owned CPU children; GPU/container gates are separate."""
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
import time

import pytest

from pantheon.models import group_mesh, group_network
from test_model_engines import load
from test_model_group_mesh import transport, eventually
from test_model_group_network import document, issue


@pytest.fixture
def supervisor(monkeypatch):
    monkeypatch.setitem(sys.modules, 'group_network', group_network)
    return load('group_supervisor')


class CPUChild:
    def __init__(self):
        self.process = None
        self.starts = 0

    def start(self):
        self.starts += 1
        self.process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])

    def poll(self):
        return self.process.poll() if self.process else None

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait(timeout=2)


def launch(supervisor, transport, probes, *, count=3, startup_timeout=2):
    topology, contexts, _ = transport
    runs, children, cancels, threads, errors = [], [], [], [], []
    for rank in range(count):
        mesh = group_mesh.PeerMesh(topology, rank, *contexts[rank],
            startup_timeout=.8, peer_timeout=.6, interval=.1)
        child, cancel = CPUChild(), threading.Event()
        run = supervisor.Supervisor(mesh, child, probes[rank], startup_timeout=startup_timeout,
            probe_interval=.03, probe_timeout=.2, monitor_interval=.02)
        runs.append(run); children.append(child); cancels.append(cancel)
        def execute(current=run, stop=cancel):
            try:
                current.run(stop)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=execute)
        threads.append(thread)
        thread.start()
    return runs, children, cancels, threads, errors


def finish(state):
    runs, children, cancels, threads, errors = state
    for cancel in cancels:
        cancel.set()
    for run in runs:
        run.mesh.fail('test-cleanup')
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    for child in children:
        child.stop()


@pytest.mark.parametrize('fault', ['engine_exit', 'peer_loss', 'failed_probe', 'cancel'])
def test_cohort_gate_stops_owned_children_after_original_rank_failure(supervisor, transport, fault):
    loading_until = time.monotonic() + .4
    fail_probe = threading.Event()
    def probe():
        if time.monotonic() < loading_until or fail_probe.is_set():
            raise ValueError('engine not ready')
    state = launch(supervisor, transport, [probe, probe, probe])
    runs, children, cancels, threads, errors = state
    untouched = CPUChild(); untouched.start()
    try:
        eventually(lambda: all(child.starts == 1 for child in children))
        assert not any(run.ready() for run in runs)
        eventually(lambda: all(run.ready() for run in runs))
        if fault == 'engine_exit': children[1].process.kill()
        elif fault == 'peer_loss': runs[1].mesh.fail('disconnected')
        elif fault == 'failed_probe': fail_probe.set()
        else: cancels[1].set()
        eventually(lambda: all(child.poll() is not None for child in children))
        assert not any(run.ready() for run in runs)
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        assert all(child.starts == 1 for child in children)
        assert untouched.poll() is None
        assert len(transport[2]) == 3  # No replacement connections/ranks.
        assert errors  # Peers fail even when the initiating rank was cancelled.
        with pytest.raises(RuntimeError, match='reused'):
            runs[0].run(threading.Event())
    finally:
        finish(state)
        untouched.stop()


def test_stalled_probe_cannot_keep_engine_or_old_readiness_alive(supervisor, transport):
    entered, release, stall = threading.Event(), threading.Event(), threading.Event()
    def probe():
        if stall.is_set():
            entered.set()
            release.wait(3)
    state = launch(supervisor, transport, [lambda: None, probe, lambda: None])
    runs, children, *_ = state
    try:
        eventually(lambda: all(run.ready() for run in runs))
        stall.set()
        assert entered.wait(1)
        eventually(lambda: all(child.poll() is not None for child in children))
        assert not any(run.ready() for run in runs)
        release.set()  # A late successful probe must not restore the group.
        for thread in state[3]: thread.join(3)
        assert not any(run.ready() for run in runs)
    finally:
        release.set()
        finish(state)


@pytest.mark.parametrize('missing_peer', [True, False])
def test_incomplete_authentication_or_warmup_expires_without_publication(supervisor, transport, missing_peer):
    def cold(): raise ValueError('still loading')
    state = launch(supervisor, transport, [cold] * 3, count=2 if missing_peer else 3, startup_timeout=.6)
    try:
        for thread in state[3]:
            thread.join(3)
            assert not thread.is_alive()
        assert not any(run.ready() for run in state[0])
        assert all(child.starts == (0 if missing_peer else 1) for child in state[1])
        assert all(not child.process or child.poll() is not None for child in state[1])
        assert state[4]
    finally:
        finish(state)


@pytest.fixture
def sealed(tmp_path):
    topology = group_network.PeerTopology(document())
    issue(tmp_path, topology)
    root = tmp_path / 'sealed'; root.mkdir(mode=0o700)
    ca = (tmp_path / 'ca.pem').read_text()
    manifest = dict(protocol=1, rank=0, topology=topology.document(),
                    ca_sha256=hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca)).hexdigest())
    for name, value in {'group-peer.json': json.dumps(manifest), 'ca.pem': ca,
        'certificate.pem': (tmp_path / '0.crt').read_text(), 'key.pem': (tmp_path / '0.key').read_text()}.items():
        (root / name).write_text(value)
        (root / name).chmod(0o400)
    root.chmod(0o500)
    identity = dict(PANTHEON_FLEET_ID=topology.document()['owner'],
        PANTHEON_NODE_ID='n_first', PANTHEON_INSTANCE_GENERATION='1')
    try:
        yield root, identity, topology.document()
    finally:
        root.chmod(0o700)


def test_runtime_bundle_loads_only_original_plan_without_exporting_material(supervisor, sealed):
    root, identity, topology = sealed
    loaded, rank, server, client = supervisor.credentials(root, identity, topology)
    assert loaded.document() == topology and rank == 0
    assert client.check_hostname and server.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize('change', ['owner', 'node', 'generation', 'plan', 'pin', 'symlink', 'writable', 'extra'])
def test_runtime_bundle_rejects_wrong_identity_and_modified_files(supervisor, sealed, change):
    root, identity, topology = sealed
    if change in {'owner', 'node', 'generation'}:
        identity[{'owner':'PANTHEON_FLEET_ID','node':'PANTHEON_NODE_ID','generation':'PANTHEON_INSTANCE_GENERATION'}[change]] = 'other'
    elif change == 'plan': topology['launch_sha256'] = 'e' * 64
    elif change == 'writable': (root / 'key.pem').chmod(0o600)
    else:
        root.chmod(0o700)
        if change == 'pin':
            p = root / 'group-peer.json'; p.chmod(0o600)
            manifest = json.loads(p.read_text()); manifest['ca_sha256'] = 'f' * 64
            p.write_text(json.dumps(manifest)); p.chmod(0o400)
        elif change == 'symlink':
            (root / 'key.pem').unlink(); (root / 'key.pem').symlink_to('../0.key')
        else: (root / 'unexpected').write_text('not exported by Runner')
        root.chmod(0o500)
    with pytest.raises((ValueError, OSError)):
        supervisor.credentials(root, identity, topology)


def test_linux_engine_refuses_callers_process_group(supervisor):
    # The test runner's group must never be signalled by an in-process test.
    if os.getpgrp() != os.getpid() or not Path('/proc/self/stat').is_file():
        with pytest.raises(ValueError, match='process group'):
            supervisor.LinuxEngine(['never-start'], {})
