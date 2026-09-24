"""Entrypoint contracts; GPU and Linux listener ownership are separate gates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import ssl
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from pantheon.models import group_mesh, group_network
from test_model_engines import load
from test_model_group_network import issue
from test_model_sglang_group import group, plan, record


@pytest.fixture
def runtime(monkeypatch, group):
    monkeypatch.setitem(sys.modules, 'group_mesh', group_mesh)
    monkeypatch.setitem(sys.modules, 'group_supervisor', load('group_supervisor'))
    monkeypatch.setitem(sys.modules, 'sglang_group', group)
    return load('sglang_group_runtime')


@pytest.mark.parametrize('rank', [0, 1])
@pytest.mark.parametrize('identity', ['original', 'foreign', 'replaced', 'gone'])
def test_probe_requires_original_listener_before_and_after_http(runtime, monkeypatch, rank, identity):
    original = (('1234', 100, '123'),)
    sequence = iter([None if identity == 'foreign' else original,
                     None if identity == 'gone' else (('5678', 101, '124'),) if identity == 'replaced' else original])
    engine = SimpleNamespace(listener_identity=lambda port: next(sequence))
    calls = []
    monkeypatch.setattr(runtime.sglang_group, 'ready_rank', lambda *args: calls.append(args))
    if identity == 'original':
        runtime.probe_engine(engine, 'b' * 64, rank)
    else:
        with pytest.raises(ValueError, match='Original engine'):
            runtime.probe_engine(engine, 'b' * 64, rank)
    assert calls == ([] if identity == 'foreign' else [(30000, 'b' * 64, rank)])


def test_status_is_authenticated_live_and_generation_bound(runtime, monkeypatch):
    current = SimpleNamespace(value=False)
    token = 'a' * 32
    identity = dict(instance_id='b' * 32, generation=3)
    server, thread = runtime.serve_status(SimpleNamespace(ready=lambda: current.value), 0, token, identity)
    port = server.server_address[1]
    def query(path='/ready', credential=token):
        request = Request(f'http://127.0.0.1:{port}{path}', headers={'X-Pantheon-App-Token': credential})
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            assert response.headers['Cache-Control'] == 'no-store'
            return response.code, json.loads(response.read())
    try:
        assert query(credential='') == (401, {'error': 'Unauthorized'})
        assert query(credential='é' * 32)[0] == 401
        assert query('/v1/chat/completions')[0] == 404
        assert query() == (503, dict(protocol=1, ready=False, **identity))
        current.value = True
        assert query() == (200, dict(protocol=1, ready=True, **identity))
        for key, value in dict(PANTHEON_PORT_HTTP=str(port), PANTHEON_APP_RPC_TOKEN=token,
            PANTHEON_INSTANCE_ID=identity['instance_id'], PANTHEON_INSTANCE_GENERATION='3').items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(sys, 'argv', ['sglang_group_runtime.py', 'ready'])
        runtime.main()
        monkeypatch.setenv('PANTHEON_INSTANCE_GENERATION', '4')
        with pytest.raises(ValueError, match='Original model cohort'):
            runtime.main()
        current.value = False
        assert query()[0] == 503
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize('rank', [0, 1])
def test_build_uses_sealed_plan_and_strips_engine_credentials(runtime, monkeypatch, tmp_path, rank):
    value, snapshot = plan(), record()
    topology = runtime.sglang_group._validate(value, snapshot)[3]
    issue(tmp_path, topology)
    directory = tmp_path / 'sealed'; directory.mkdir()
    ca = (tmp_path / 'ca.pem').read_text()
    manifest = dict(protocol=1, rank=rank, topology=topology.document(),
        ca_sha256=hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca)).hexdigest())
    for name, content in {'group-peer.json': json.dumps(manifest), 'ca.pem': ca,
        'certificate.pem': (tmp_path / f'{rank}.crt').read_text(),
        'key.pem': (tmp_path / f'{rank}.key').read_text()}.items():
        (directory / name).write_text(content); (directory / name).chmod(0o400)
    directory.chmod(0o500)
    environment = dict(PANTHEON_GROUP_CREDENTIALS=str(directory), PANTHEON_FLEET_ID=value['owner'],
        PANTHEON_NODE_ID=f'n_{rank}', PANTHEON_INSTANCE_GENERATION='1',
        PANTHEON_APP_RPC_TOKEN='private-status-token', PANTHEON_MODEL_CREDENTIALS='/other-private',
        NCCL_SOCKET_IFNAME='public0', PANTHEON_GROUP_UNTRUSTED='ignored', PATH='/usr/bin')
    interfaces = []
    monkeypatch.setattr(runtime, 'local_interface', lambda member: interfaces.append(member))
    monkeypatch.setattr(runtime, 'assigned_capacities', lambda member: [24 << 30])
    monkeypatch.setattr(runtime, 'LinuxEngine', lambda argv, env: SimpleNamespace(argv=argv, env=env))
    try:
        run, checked, found_rank = runtime.build(value, snapshot, environment)
        assert checked.document() == topology.document() and found_rank == rank
        assert interfaces == [value['members'][rank]]
        assert run.engine.env['NCCL_SOCKET_IFNAME'] == '=eth0'
        assert run.engine.env['CUDA_VISIBLE_DEVICES'] == f'GPU-{rank}'
        assert not any(key.startswith('PANTHEON_GROUP_') or key in {
            'PANTHEON_APP_RPC_TOKEN', 'PANTHEON_MODEL_CREDENTIALS'} for key in run.engine.env)
        wrong = deepcopy(value); wrong['context_length'] = 8192
        with pytest.raises(ValueError):
            runtime.build(wrong, snapshot, environment)
        monkeypatch.setattr(runtime, 'assigned_capacities', lambda member: [48 << 30])
        with pytest.raises(ValueError, match='capacity changed'):
            runtime.build(value, snapshot, environment)
    finally:
        directory.chmod(0o700)


@pytest.mark.parametrize('output', ['GPU-other, 24576\n', 'GPU-0, 24576\nGPU-0, 24576\n', ''])
def test_gpu_measurement_rejects_changed_or_ambiguous_identity(runtime, monkeypatch, output):
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=output))
    with pytest.raises(ValueError, match='GPU identity'):
        runtime.assigned_capacities(plan()['members'][0])
