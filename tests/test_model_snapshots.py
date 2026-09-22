import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import threading

import pytest

from test_model_engines import load

snapshots, artifacts = load('snapshots'), load('artifacts')


def bundle(tmp_path, extra=None):
    config = dict(model_type='qwen2', num_hidden_layers=24, num_attention_heads=14,
                  num_key_value_heads=2, hidden_size=896)
    header = json.dumps({'test': {'dtype': 'F16', 'shape': [2], 'data_offsets': [0, 4]}}).encode()
    entries = {'config.json': json.dumps(config).encode(), 'tokenizer.json': b'{}',
               'model.safetensors': len(header).to_bytes(8, 'little') + header + b'1234', **(extra or {})}
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode='w:gz') as tar:
        for name, data in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    value = content.getvalue()
    digest = hashlib.sha256(value).hexdigest()
    (tmp_path / 'blobs').mkdir(exist_ok=True)
    (tmp_path / 'blobs' / digest).write_bytes(value)
    return dict(url='https://example.invalid/model.tgz', sha256=digest, size=len(value),
                revision='commit', name='model.tgz', format='safetensors.tar.gz')


def test_prepared_snapshot_is_offline_immutable_and_accounts_for_kv(tmp_path):
    source = bundle(tmp_path)
    cache = snapshots.SnapshotCache(tmp_path, artifacts)
    result = cache.fetch(source, threading.Event(), lambda *a: None)
    record = snapshots.snapshot(tmp_path, source['sha256'])
    estimate = snapshots.memory_estimate(record, 4096, 2)
    assert estimate['kv_bytes'] == 2 * 24 * 2 * 64 * 4096 * 2 * 2
    assert estimate['estimated_bytes'] == record['weights_bytes'] + estimate['kv_bytes'] + (2 << 30)
    (tmp_path / 'blobs' / source['sha256']).unlink()
    assert cache.fetch(source, threading.Event(), lambda *a: None) == result
    (result / 'model.safetensors').chmod(0o600)
    (result / 'model.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='files changed'):
        cache.fetch(source, threading.Event(), lambda *a: None)


@pytest.mark.parametrize('name', ['../escape', '/outside', 'code.py', 'pytorch_model.bin', 'snapshot.json', 'CONFIG.JSON'])
def test_model_bundle_rejects_code_pickle_paths_and_case_collisions(tmp_path, name):
    source = bundle(tmp_path, {name: b'bad'})
    with pytest.raises(ValueError):
        snapshots.SnapshotCache(tmp_path, artifacts).fetch(source, threading.Event(), lambda *a: None)
    assert snapshots.snapshot(tmp_path, source['sha256']) is None


def test_sglang_recipe_has_exact_read_only_files_and_constrained_device_budget(tmp_path, monkeypatch):
    from pantheon.models.managed import package, validate
    config = dict(recipe_id='sglang-0.5.20-linux-amd64', context_length=4096, parallel=2, keep_alive_seconds=0,
        model_artifact_sha256='a' * 64,
        resources=dict(memory_bytes=10 << 30, devices=[dict(id='GPU-uuid', backend='cuda', memory_bytes=16 << 30, exclusive=True)]))
    with package(config, 'linux-amd64') as root:
        definition = json.loads((root/'fleet.json').read_text())
        component = definition['components'][0]
        assert '@sha256:' in component['image'] and component['runtime'] == 'container'
        assert component['read_only_mounts'] == {'package':'/fleet/package', 'cache/snapshots/' + 'a'*64: '/fleet/weights'}
        assert component['resources'] == config['resources']
        assert definition['dependencies']['container_engine']['provision'] == 'never'
        assert not definition.get('hooks')
    with pytest.raises(ValueError, match='resident'):
        validate({**config, 'keep_alive_seconds':300}, 'linux-amd64')
    monkeypatch.setitem(sys.modules, 'snapshots', snapshots)
    driver = load('sglang_runtime')
    record = dict(sha256='a'*64, weights_bytes=1 << 30, config=dict(model_type='qwen2', num_hidden_layers=24,
        num_attention_heads=14, num_key_value_heads=2, hidden_size=896))
    argv = driver.launch(config, record, 24 << 30)
    assert '--trust-remote-code' not in argv
    assert argv[argv.index('--max-total-tokens')+1] == '8192'
    assert argv[argv.index('--dtype')+1] == 'float16'
    with pytest.raises(ValueError, match='budget'):
        driver.launch({**config, 'context_length':1 << 20}, record, 24 << 30)


def test_snapshot_refuses_unknown_memory_architecture():
    with pytest.raises(ValueError, match='estimator'):
        snapshots.memory_estimate(dict(config=dict(model_type='unknown'), weights_bytes=100), 4096, 1)
