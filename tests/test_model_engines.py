import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import threading
import zipfile

import pytest


def test_connector_prepares_recipe_with_dotted_version(tmp_path, monkeypatch):
    from test_model_services import connector_module
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setenv('PANTHEON_APP_SCOPE', 'model-preparation')
    connector = connector_module.Connector(tmp_path / 'connector')
    recipes = connector.module('engines')
    monkeypatch.setattr(recipes, 'native_platform', lambda: 'darwin-arm64')
    jobs = connector.engine_downloads()
    requested = []
    def fetch(source, cancelled, progress):
        requested.append(source)
        progress('ready', source['size'])
        return tmp_path / 'prepared'
    monkeypatch.setattr(jobs.cache, 'fetch', fetch)
    try:
        recipe_id = 'ollama-0.34.2-darwin'
        assert connector.prepare_engine(recipe_id, resume=True) == {'job_id': recipe_id}
        for _ in range(200):
            if jobs.list()[0]['state'] == 'ready': break
            threading.Event().wait(.01)
        assert jobs.list()[0]['state'] == 'ready'
        assert requested == [recipes.recipe(recipe_id)['source']]
        connector.prepare_engine(recipe_id, resume=True)
        assert len(requested) == 1
    finally:
        jobs.close()
        connector.downloads().close()


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'apps/model-service' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engines, artifacts = load('engines'), load('artifacts')


def archive(path, entries):
    with tarfile.open(path, 'w:gz') as tar:
        for name, kind, value in entries:
            info = tarfile.TarInfo(name)
            info.mode = 0o755
            if kind == 'file':
                info.size = len(value)
                tar.addfile(info, io.BytesIO(value))
            else:
                info.type = {'symlink': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE, 'device': tarfile.CHRTYPE}[kind]
                info.linkname = value
                tar.addfile(info)


@pytest.mark.parametrize('entries', [
    [('../outside', 'file', b'bad')],
    [('/absolute', 'file', b'bad')],
    [('C:/windows', 'file', b'bad')],
    [('CON.txt', 'file', b'bad')],
    [('escape', 'symlink', '../../outside')],
    [('escape', 'hardlink', '../outside')],
    [('device', 'device', '')],
    [('Engine', 'file', b'one'), ('engine', 'file', b'two')],
    [('parent', 'symlink', 'target'), ('parent/file', 'file', b'data')],
    [('a', 'symlink', 'b'), ('b', 'symlink', 'a')],
    [('dangling', 'symlink', 'missing')],
    [('dir/file', 'file', b'data'), ('alias', 'symlink', 'dir')],
    [('lib/escape', 'symlink', '../../outside'), ('alias', 'symlink', 'lib/escape')],
])
def test_archive_never_writes_outside_or_through_links(tmp_path, entries):
    path = tmp_path / 'input.tgz'; archive(path, entries)
    out = tmp_path / 'out'; out.mkdir()
    with pytest.raises(ValueError):
        engines.extract(path, out, 'tar.gz', threading.Event())
    assert not (tmp_path / 'outside').exists()


def test_archive_keeps_internal_libraries_and_limits_expansion(tmp_path):
    path = tmp_path / 'input.tgz'
    archive(path, [('lib/version.dylib', 'file', b'library'),
                   ('lib/current.dylib', 'symlink', 'version.dylib'),
                   ('engine', 'hardlink', 'lib/version.dylib')])
    out = tmp_path / 'out'; out.mkdir()
    files = engines.extract(path, out, 'tar.gz', threading.Event())
    assert len(files) == 3
    assert (out / 'lib/current.dylib').read_bytes() == b'library'
    assert not (out / 'lib/current.dylib').is_symlink()
    limited = tmp_path / 'limited'; limited.mkdir()
    with pytest.raises(ValueError, match='size limit'):
        engines.extract(path, limited, 'tar.gz', threading.Event(), max_bytes=2)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(InterruptedError):
        engines.extract(path, limited, 'tar.gz', cancelled)


def test_archive_resolves_forward_alias_chains_without_symlinks(tmp_path):
    path = tmp_path / 'input.tgz'
    archive(path, [('bin/python_', 'symlink', '../runtime/python'),
                   ('runtime/python', 'symlink', 'python3.11'),
                   ('runtime/python3.11', 'file', b'python')])
    out = tmp_path / 'out'; out.mkdir()
    files = engines.extract(path, out, 'tar.gz', threading.Event())
    assert len(files) == 3
    assert (out / 'bin/python_').read_bytes() == b'python'
    assert not (out / 'bin/python_').is_symlink()


def test_windows_zip_preparation_is_portable_without_symlink_privileges(tmp_path):
    path = tmp_path / 'engine.zip'
    with zipfile.ZipFile(path, 'w') as zipped:
        zipped.writestr('ollama.exe', b'windows-executable')
        zipped.writestr('lib/runtime.dll', b'library')
    out = tmp_path / 'out'; out.mkdir()
    assert len(engines.extract(path, out, 'zip', threading.Event())) == 2


def test_engine_preparation_pins_identity_and_reuses_offline(tmp_path, monkeypatch):
    payload = tmp_path / 'input.tgz'; archive(payload, [('ollama', 'file', b'engine')])
    source = dict(url='https://example.invalid/engine.tgz', sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
                  size=payload.stat().st_size, name='engine.tgz', revision='1', format='tar.gz')
    selected = dict(id='ollama-test', engine='ollama', version='1', platforms=[engines.native_platform()],
                    executable='ollama', source=source)
    monkeypatch.setattr(engines, 'catalog', lambda: [selected])
    class Blobs:
        calls = 0
        def fetch(self, value, cancel, progress):
            assert value == source
            self.calls += 1
            progress('ready', source['size'])
            return payload
    blobs = Blobs()
    cache = engines.EngineCache(tmp_path, blobs, artifacts.file_lock, artifacts.atomic_json)
    states = []
    binary = cache.fetch(source, threading.Event(), lambda state, done: states.append(state))
    assert binary.read_bytes() == b'engine' and states == ['installing', 'ready']
    payload.unlink()
    assert cache.fetch(source, threading.Event(), lambda *args: None) == binary
    assert blobs.calls == 1
    with pytest.raises(ValueError, match='immutable'):
        cache.fetch({**source, 'sha256': '0' * 64}, threading.Event(), lambda *args: None)
    binary.chmod(0o600); binary.write_bytes(b'modified')
    with pytest.raises(ValueError, match='files changed'):
        cache.fetch(source, threading.Event(), lambda *args: None)


def test_recipe_selection_cannot_execute_arbitrary_downloads_or_switch_os():
    for value in ('../../user/binary', 'latest', '', None):
        with pytest.raises(ValueError):
            engines.recipe(value)
    with pytest.raises(ValueError, match='platform'):
        engines.recipe('ollama-0.34.2-darwin', target='linux-amd64')


def managed_config():
    return dict(recipe_id='ollama-0.34.2-darwin', context_length=4096, parallel=1, keep_alive_seconds=300,
                resources=dict(memory_bytes=4 << 30, devices=[dict(id='apple-metal', backend='metal', memory_bytes=4 << 30, exclusive=False)]))


@pytest.mark.parametrize('recipe', ['ollama-0.34.2-darwin', 'llmster-0.0.25-1-darwin-arm64'])
def test_explicit_lifetime_is_validated_and_preserved_in_engine_package(recipe, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'engines', engines)
    from pantheon.models.managed import package, validate
    config = {**managed_config(), 'recipe_id': recipe}
    assert 'load_policy' not in validate(config, 'darwin-arm64')
    for policy, ttl in [('on_demand', 0), ('resident', 0), ('warm', 30), ('manual', 300)]:
        value = {**config, 'load_policy': policy, 'keep_alive_seconds': ttl}
        with package(value, 'darwin-arm64') as directory:
            parsed, _ = load('managed_engine').configuration(directory / 'engine-config.json')
            assert parsed['load_policy'] == policy and parsed['keep_alive_seconds'] == ttl
    for policy, ttl in [('warm', 0), ('resident', 30), ('on_demand', 30), ([], 0), ({}, 0), (None, 0)]:
        with pytest.raises(ValueError):
            validate({**config, 'load_policy': policy, 'keep_alive_seconds': ttl}, 'darwin-arm64')


def test_managed_package_is_deterministic_with_exact_budget_and_no_installer():
    from pantheon.models.managed import package, validate
    from pantheon.apps.lifecycle import build_artifact
    config = managed_config()
    with package(config, 'darwin-arm64') as first, package(config, 'darwin-arm64') as second:
        assert build_artifact(first) == build_artifact(second)
        definition = json.loads((first / 'fleet.json').read_text())
        assert not definition.get('hooks')
        assert definition['components'][0]['resources'] == config['resources']
        assert definition['components'][0]['argv'] == ['python3', '${PACKAGE}/managed_engine.py', 'start']
        assert 'resources' not in json.loads((first / 'engine-config.json').read_text())
    for changed in ({**config, 'argv': ['bash']}, {**config, 'parallel': True},
                    {**config, 'resources': {**config['resources'], 'memory_bytes': 1 << 30}}):
        with pytest.raises(ValueError):
            validate(changed, 'darwin-arm64')


@pytest.mark.asyncio
async def test_managed_create_prepares_without_starting_or_adopting_an_engine():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pantheon.models.manager import ModelServiceManager
    config = managed_config()
    rows = []
    async def save(row):
        copy = json.loads(json.dumps(row)); copy['revision'] += 1
        rows.append(json.loads(json.dumps(copy)))
        return copy
    client = SimpleNamespace(deployments=AsyncMock(return_value=[]), save=save)
    manager = ModelServiceManager(client, SimpleNamespace())
    manager.node = AsyncMock(return_value={'name': 'Mac', 'capability': {'os': 'darwin', 'arch': 'arm64'}})
    manager.ensure = AsyncMock(return_value={'node_id': 'mac', 'instance_id': 'connector', 'revision': 'a' * 64, 'generation': 1})
    manager.rpc = AsyncMock(return_value={'job_id': config['recipe_id']})
    row = await manager.create_managed('my-model', 'My model', 'mac', config)
    assert row['state'] == 'draft' and row['mode'] == 'managed'
    assert not row.get('engine_binding')
    assert rows[0].get('binding') is None and rows[-1]['binding']['instance_id'] == 'connector'
    assert manager.ensure.await_count == 1
    assert manager.rpc.call_args.args[1:] == ('engines_prepare', {'recipe_id': config['recipe_id'], 'resume': True})
    client.deployments.return_value = [row]
    with pytest.raises(ValueError, match='different configuration'):
        await manager.create_managed('my-model', 'My model', 'mac', {**config, 'parallel': 2})


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['engine_pending', 'connector_ack', 'engine_ack', 'connector_save'])
async def test_failed_stop_retries_exact_stopped_generations(monkeypatch, failure):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import pantheon.models.manager as module
    binding = dict(node_id='mac', instance_id='connector', revision='a' * 64, generation=1)
    row = dict(deployment_id='model', node_id='mac', mode='managed', state='ready', binding=binding,
               engine_binding={**binding, 'instance_id': 'engine', 'revision': 'b' * 64}, revision=1)
    instances = {
        'connector': dict(digest='a' * 64, app_id='model-service', scope='model-model', generation=1, state='ready'),
        'engine': dict(digest='b' * 64, app_id='model-service', scope='engine-model', generation=1, state='ready'),
    }
    events, failed = [], False
    class Lifecycle:
        def __init__(self, resolver): pass
        async def status(self, node):
            return {'instances': json.loads(json.dumps(instances))}
        async def submit(self, node, action, digest, **args):
            instance_id = 'connector' if args['scope'] == 'model-model' else 'engine'
            assert args['generation'] == instances[instance_id]['generation']
            events.append(instance_id)
            return instance_id
    async def wait(node, instance_id):
        nonlocal failed
        if failure == 'engine_pending' and instance_id == 'engine' and not failed:
            failed = True
            raise RuntimeError('engine stop not confirmed')
        instances[instance_id].update(state='stopped', generation=2, resources=[], reservations=[])
        if failure == instance_id + '_ack' and not failed:
            failed = True
            raise RuntimeError('stop not confirmed')
        return await Lifecycle(None).status(node)
    async def save(value):
        nonlocal failed
        if failure == 'connector_save' and value['binding']['generation'] == 2 and not failed:
            failed = True
            raise RuntimeError('registry write not confirmed')
        row.update(json.loads(json.dumps(value))); row['revision'] += 1
        return json.loads(json.dumps(row))
    client = SimpleNamespace(deployment=AsyncMock(side_effect=lambda _: json.loads(json.dumps(row))), save=save)
    manager = module.ModelServiceManager(client, SimpleNamespace())
    manager.rpc = AsyncMock(return_value={'safe_to_stop': True})
    manager.wait = wait
    monkeypatch.setattr(module, 'FleetLifecycle', Lifecycle)
    with pytest.raises(RuntimeError, match='not confirmed'):
        await manager.set_running('model', False)
    assert row['state'] == 'stopping'
    with pytest.raises(ValueError, match='Finish stopping'):
        await manager.set_running('model', True)
    result = await manager.set_running('model', False)
    assert result['state'] == 'stopped'
    assert result['binding']['generation'] == result['engine_binding']['generation'] == 2
    assert events == (['connector', 'engine', 'engine'] if failure == 'engine_pending' else ['connector', 'engine'])
    assert manager.rpc.await_count == 1


@pytest.mark.parametrize('changed', [
    {'generation': 2, 'state': 'ready'}, {'generation': 3, 'state': 'stopped'},
    {'generation': 2, 'state': 'stopped', 'resources': [{'pid': 42}]},
    {'generation': 2, 'state': 'stopped', 'reservations': [{'memory_bytes': 100}]},
    {'scope': 'engine-another'}, {'app_id': 'another'}, {'digest': 'b' * 64},
])
def test_stop_recovery_never_adopts_later_live_or_unrelated_instance(changed):
    from pantheon.models.manager import ModelServiceManager
    binding = dict(instance_id='engine', revision='a' * 64, generation=1)
    instance = dict(digest='a' * 64, app_id='model-service', scope='engine-model', generation=1, state='ready')
    with pytest.raises(ValueError):
        ModelServiceManager.bound_instance({'instances': {'engine': {**instance, **changed}}}, binding, 'engine-model')


def test_llmster_scoped_runtime_preserves_shared_recipe_and_other_deployment(tmp_path, monkeypatch):
    payload = tmp_path / 'input.tgz'
    archive(payload, [('llmster', 'file', b'engine'), ('.bundle/library', 'file', b'runtime')])
    source = dict(url='https://example.invalid/engine.tgz', sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
                  size=payload.stat().st_size, name='engine.tgz', revision='1', format='tar.gz')
    selected = dict(id='llmster-test', engine='lmstudio', version='1', platforms=[engines.native_platform()],
                    executable='llmster', source=source)
    monkeypatch.setattr(engines, 'catalog', lambda: [selected])
    class Blobs:
        def fetch(self, source, cancel, progress): return payload
    cache = engines.EngineCache(tmp_path, Blobs(), artifacts.file_lock, artifacts.atomic_json, 'engine-one')
    first = cache.fetch(source, threading.Event(), lambda *args: None)
    shared = engines.installed(tmp_path, selected)
    assert first != shared
    (first.parent / '.bundle/library').unlink()  # Vendor consumes its scoped bundle.
    assert (shared.parent / '.bundle/library').read_bytes() == b'runtime'
    assert cache.fetch(source, threading.Event(), lambda *args: None) == first
    cache.scope = 'engine-two'
    second = cache.fetch(source, threading.Event(), lambda *args: None)
    assert second != first and (second.parent / '.bundle/library').read_bytes() == b'runtime'
    assert engines.installed(tmp_path, selected) == shared


def test_llmster_settings_only_change_owned_home_and_disable_implicit_loading(tmp_path):
    module = load('llmster_runtime')
    config = managed_config()
    module.configure(tmp_path, config, 32123, 'recipe')
    settings = json.loads((tmp_path / '.lmstudio/settings.json').read_text())
    server = json.loads((tmp_path / '.lmstudio/.internal/http-server-config.json').read_text())
    assert not settings['autoLoadBundledLLM']
    assert not settings['enableLocalService']
    assert not settings['developer']['autoUpdateExtensionPacks']
    assert not settings['developer']['allowDevelopmentPlugins']
    assert server['networkInterface'] == '127.0.0.1' and server['port'] == 32123
    assert not server['justInTimeModelLoading'] and not server['logSensitiveData']
    assert not (tmp_path / '.zshrc').exists()
    from pantheon.models.managed import validate, package
    config['recipe_id'] = 'llmster-0.0.25-1-darwin-arm64'
    with package(config, 'darwin-arm64') as directory:
        assert (directory / 'llmster_runtime.py').is_file()
    for changed in ({**config, 'parallel': 2}, {**config, 'keep_alive_seconds': 0}):
        with pytest.raises(ValueError, match='llmster'):
            validate(changed, 'darwin-arm64')


@pytest.mark.parametrize('policy,ttl', [('manual', 300), ('warm', 30), ('resident', 0), ('on_demand', 0)])
def test_llmster_lifetime_sentinel_never_invalidates_vendor_settings(tmp_path, policy, ttl):
    config = {**managed_config(), 'keep_alive_seconds': ttl, 'load_policy': policy}
    load('llmster_runtime').configure(tmp_path, config, 32123, 'recipe')
    settings = json.loads((tmp_path / '.lmstudio/settings.json').read_text())
    lifetime = settings['developer']['jitModelTTL']
    # The pinned llmster Zod schema rejects zero even when enabled is false,
    # discarding the entire settings file and restoring unsafe vendor defaults.
    assert lifetime['ttlSeconds'] > 0
    assert lifetime['enabled'] is (ttl > 0)
    if ttl:
        assert lifetime['ttlSeconds'] == ttl
    assert not settings['autoLoadBundledLLM'] and not settings['developer']['autoUpdateExtensionPacks']


def test_pinned_unix_runtime_preserves_safe_file_symlink_semantics(tmp_path):
    path = tmp_path / 'input.tgz'
    archive(path, [('env/python_', 'symlink', '../base/python'),
                   ('base/python', 'symlink', 'python3.11'), ('base/python3.11', 'file', b'python')])
    out = tmp_path / 'out'; out.mkdir()
    files = engines.extract(path, out, 'tar.gz', threading.Event(), preserve_file_links=True)
    assert (out / 'env/python_').is_symlink()
    assert (out / 'env/python_').resolve() == out / 'base/python3.11'
    assert next(r for r in files if r['path'] == 'env/python_')['link'] == '../base/python3.11'
    for entries in [[('a', 'symlink', 'b'), ('b', 'symlink', 'a')],
                    [('directory/file', 'file', b'one'), ('alias', 'symlink', 'directory')],
                    [('a', 'symlink', '../../outside')]]:
        archive(path, entries)
        import tempfile
        with tempfile.TemporaryDirectory(dir=tmp_path) as bad:
            with pytest.raises(ValueError):
                engines.extract(path, bad, 'tar.gz', threading.Event(), preserve_file_links=True)


def test_cpu_only_ollama_on_linux_and_windows(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'engines', engines)
    from pantheon.models.managed import package, validate
    cpu = dict(recipe_id='ollama-0.34.2-windows-amd64', context_length=2048, parallel=1, keep_alive_seconds=0,
               load_policy='on_demand', resources=dict(memory_bytes=1 << 30, devices=[]))
    assert validate(cpu, 'windows-amd64')['resources']['devices'] == []
    assert validate({**cpu, 'recipe_id': 'ollama-0.34.2-linux-amd64'}, 'linux-amd64')['resources']['devices'] == []
    with package(cpu, 'windows-amd64') as directory:
        assert (directory / 'managed_engine.py').exists()
    # Apple silicon keeps its unified-memory Metal declaration; TP still needs GPUs.
    with pytest.raises(ValueError, match='accelerator'):
        validate({**cpu, 'recipe_id': 'ollama-0.34.2-darwin'}, 'darwin-arm64')
    with pytest.raises(ValueError):
        validate({**cpu, 'tensor_parallel_size': 1}, 'windows-amd64')


def test_windows_platform_does_not_depend_on_processor_environment(monkeypatch):
    import platform as host
    monkeypatch.setattr(host, 'system', lambda: 'Windows')
    monkeypatch.setattr(host, 'machine', lambda: '')  # PROCESSOR_ARCHITECTURE stripped by Fleet
    import sysconfig
    for build, expected in (('win-amd64', 'windows-amd64'), ('win-arm64', 'windows-arm64')):
        monkeypatch.setattr(sysconfig, 'get_platform', lambda build=build: build)
        assert engines.native_platform() == expected
