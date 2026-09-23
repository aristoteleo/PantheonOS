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


def parallel_config():
    return dict(recipe_id='sglang-0.5.20-linux-amd64', context_length=4096,
                parallel=2, keep_alive_seconds=0, tensor_parallel_size=2,
                model_artifact_sha256='a' * 64,
                resources=dict(memory_bytes=10 << 30, devices=[
                    dict(id='GPU-' + str(i), backend='cuda', memory_bytes=12 << 30, exclusive=True)
                    for i in range(2)]))


def test_tensor_parallel_launch_checks_every_rank_and_uses_exact_devices(monkeypatch):
    from pantheon.models.managed import package, validate
    config = parallel_config()
    with package(config, 'linux-amd64') as root:
        component = json.loads((root / 'fleet.json').read_text())['components'][0]
        assert component['resources']['devices'] == config['resources']['devices']
        assert json.loads((root / 'engine-config.json').read_text())['tensor_parallel_size'] == 2
    monkeypatch.setitem(sys.modules, 'snapshots', snapshots)
    driver = load('sglang_runtime')
    record = dict(sha256='a'*64, weights_bytes=2 << 30, tensor_parallel_weights={'2': 1 << 30},
                  config=dict(model_type='qwen2', num_hidden_layers=24,
                              num_attention_heads=16, num_key_value_heads=2, hidden_size=1024))
    argv = driver.launch(config, record, [24 << 30, 24 << 30])
    assert argv[argv.index('--tensor-parallel-size') + 1] == '2'
    config['resources']['devices'][1]['memory_bytes'] = 2 << 30
    with pytest.raises(ValueError, match='GPU budget'):
        driver.launch(config, record, [24 << 30, 24 << 30])
    config = parallel_config()
    config['resources']['devices'][1]['id'] = config['resources']['devices'][0]['id']
    with pytest.raises(ValueError, match='distinct'):
        validate(config, 'linux-amd64')
    config = parallel_config()
    config['resources']['devices'][1]['exclusive'] = False
    with pytest.raises(ValueError, match='exclusive'):
        validate(config, 'linux-amd64')
    config = parallel_config()
    with pytest.raises(ValueError, match='measurement'):
        driver.launch(config, record, [24 << 30])
    with pytest.raises(ValueError, match='static memory'):
        driver.launch(config, record, [24 << 30, 256 << 30])
    config['resources']['memory_bytes'] = 5 << 30
    with pytest.raises(ValueError, match='system memory'):
        driver.launch(config, record, [24 << 30, 24 << 30])


def test_kv_replication_and_invalid_attention_topology():
    record = dict(weights_bytes=1000, tensor_parallel_weights={'2': 800, '4': 600},
                  config=dict(model_type='qwen2', num_hidden_layers=2,
                              num_attention_heads=8, num_key_value_heads=1, hidden_size=64))
    two = snapshots.memory_estimate(record, 512, 2, 2)
    four = snapshots.memory_estimate(record, 512, 2, 4)
    assert two['kv_bytes'] == four['kv_bytes'] == 2 * 2 * 1 * 8 * 512 * 2 * 2
    assert two['weights_bytes'] == 800 and four['weights_bytes'] == 600
    assert four['per_device'] is True
    record['config']['num_key_value_heads'] = 3
    with pytest.raises(ValueError, match='partitioned'):
        snapshots.memory_estimate(record, 512, 2, 4)
    record['config']['num_key_value_heads'] = 1
    record['config']['num_attention_heads'] = 14
    with pytest.raises(ValueError, match='partitioned'):
        snapshots.memory_estimate(record, 512, 2, 4)


def test_partition_estimate_counts_replicated_tensors_and_rejects_overlap(tmp_path):
    tensors = {
        'model.layers.0.self_attn.q_proj.weight': {'dtype': 'F16', 'shape': [8, 8], 'data_offsets': [0, 128]},
        'model.layers.0.self_attn.k_proj.weight': {'dtype': 'F16', 'shape': [4, 8], 'data_offsets': [128, 192]},
        'model.layers.0.input_layernorm.weight': {'dtype': 'F16', 'shape': [8], 'data_offsets': [192, 208]},
    }
    def estimate():
        header = json.dumps(tensors).encode()
        data = len(header).to_bytes(8, 'little') + header + bytes(208)
        (tmp_path / 'model.safetensors').write_bytes(data)
        record = dict(config={'model_type': 'qwen2', 'num_key_value_heads': 1}, weights_bytes=len(data),
                      files=[dict(path='model.safetensors', size=len(data))])
        return snapshots.parallel_weight_estimates(tmp_path, record), len(data)
    estimates, size = estimate()
    # Q projection shards; K with only one head and the norm remain replicated.
    assert estimates == {'2': size - 64, '4': size - 96, '8': size - 112}
    tensors['model.layers.0.self_attn.k_proj.weight']['data_offsets'] = [64, 128]
    with pytest.raises(ValueError, match='Overlapping'):
        estimate()


def test_legacy_cached_snapshot_gains_parallel_metadata_without_archive(tmp_path):
    source = bundle(tmp_path)
    cache = snapshots.SnapshotCache(tmp_path, artifacts)
    directory = cache.fetch(source, threading.Event(), lambda *a: None)
    record = snapshots.snapshot(tmp_path, source['sha256'])
    record.pop('tensor_parallel_weights')
    artifacts.atomic_json(directory / 'snapshot.json', record)
    (tmp_path / 'blobs' / source['sha256']).unlink()
    before = (directory / 'model.safetensors').stat().st_mtime_ns
    updated = snapshots.prepared_parallel_snapshot(tmp_path, source['sha256'], artifacts)
    assert set(updated['tensor_parallel_weights']) == {'2', '4', '8'}
    assert (directory / 'model.safetensors').stat().st_mtime_ns == before
    assert snapshots.snapshot(tmp_path, source['sha256']) == updated
