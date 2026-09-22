"""Model operations against an explicitly owned, pinned llmster daemon.

The CLI comes from the prepared recipe and receives fixed arguments. It never
starts/stops a daemon, searches for weights, downloads, or reads a user's HOME.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import time


class LLMsterModels:
    def __init__(self, control):
        self.control = control

    def owned(self):
        config, port = self.control.config()
        connector = self.control.connector
        cache = connector.downloads().cache.root.parent
        runtime = connector.module('llmster_runtime')
        home = runtime.home_for(cache, config['scope'])
        runtime.ready(home, port, config['recipe_id'])
        engines = connector.module('engines')
        binary = engines.prepared(cache, engines.recipe(config['recipe_id']), config['scope'], verify_files=False)
        if not binary:
            raise ValueError('Owned llmster runtime is no longer prepared')
        cli = binary.parent / '.bundle' / 'lms'
        if not cli.is_file():
            raise ValueError('Pinned llmster CLI is unavailable')
        return config, home, cli

    def command(self, args):
        _, home, cli = self.owned()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('LMS_', 'LMSTUDIO_'))
               and k != 'PANTHEON_APP_RPC_TOKEN'}
        env.update(HOME=str(home), LMS_DAEMON_DISABLE_UPDATES='1', LMS_SERVER_HOST='127.0.0.1')
        # Vendor progress can be verbose; never retain its output in browser
        # history, process logs or memory. Exact state is verified via its API.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([str(cli), *args], env=env, cwd=cli.parent,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=output, timeout=180)
            if result.returncode:
                raise ValueError('Owned llmster could not complete this model operation')

    def catalog(self):
        rows = self.control.request('/api/v1/models', timeout=5).get('models')
        if not isinstance(rows, list):
            raise ValueError('Owned llmster returned an invalid model catalog')
        return rows

    def check_file(self, metadata):
        config, _, _ = self.owned()
        root = self.control.connector.downloads().cache.root.parent
        runtime = self.control.connector.module('llmster_runtime')
        path = runtime.home_for(root, config['scope']) / '.lmstudio' / 'models' / 'fleet' / metadata['artifact']['sha256'] / 'model.gguf'
        if path.is_symlink() or not path.is_file():
            raise ValueError('Owned llmster model file is unavailable')
        stat = path.stat()
        if stat.st_size != metadata['artifact']['size'] or stat.st_mtime_ns != metadata['file_mtime_ns']:
            raise ValueError('Owned llmster model file changed; verify and import it again')

    def imported(self, path, source, name):
        # Preserve the content-addressed cache; lms import defaults to MOVING
        # files, so always use an explicit hard-link import of our GGUF alias.
        staging = self.control.directory / 'import' / source['sha256']
        staging.mkdir(parents=True, exist_ok=True)
        alias = staging / 'model.gguf'
        if alias.exists() or alias.is_symlink():
            if alias.is_symlink() or not os.path.samefile(alias, path):
                raise ValueError('Owned import staging file changed')
        else:
            os.link(path, alias)
        namespace = 'fleet/' + source['sha256']
        config, home, _ = self.owned()
        imported = home / '.lmstudio' / 'models' / namespace / 'model.gguf'
        if not imported.exists() and not imported.is_symlink():
            self.command(['import', str(alias), '--hard-link', '--user-repo', namespace, '--yes'])
        if imported.is_symlink() or not imported.is_file() or not os.path.samefile(imported, path):
            raise ValueError('llmster did not preserve the verified artifact identity')
        # The pinned vendor watches imports asynchronously. Bounded catalog
        # reconciliation is read-only and does not repeat the import command.
        row = None
        for _ in range(30):
            # The pinned API groups GGUF variants by repository and returns
            # publisher/key separately; the key is the immutable SHA namespace.
            row = next((m for m in self.catalog() if m.get('publisher') == 'fleet'
                        and m.get('key') == source['sha256']), None)
            if row:
                break
            time.sleep(.2)
        if not row or row.get('format') != 'gguf' or row.get('size_bytes') != source['size']:
            raise ValueError('Owned llmster did not confirm the imported model')
        return dict(id=name, name=source['name'], context_length=config['context_length'],
                    artifact={k: source[k] for k in ('sha256', 'size', 'revision', 'format')},
                    engine_key=row['key'], file_mtime_ns=imported.stat().st_mtime_ns,
                    details={'format': 'gguf', 'quantization_level': (row.get('quantization') or {}).get('name')})

    def observed(self, models):
        config, _ = self.control.config()
        rows = {m.get('key'): m for m in self.catalog()}
        for model in models:
            instances = rows.get(model.get('engine_key'), {}).get('loaded_instances', [])
            loaded = next((i for i in instances if i.get('id') == model['id']), None)
            model.update(loaded=bool(loaded), memory_bytes=None, gpu_memory_bytes=None, expires_at=None)
            # This driver currently requires an owner-authorized load with
            # exact settings. A measured cold load is not permission for route
            # probes to trigger a load or to submit to an unloaded instance.
            model['inference_ready'] = bool(loaded and loaded.get('config', {}).get('context_length') == config['context_length']
                                           and loaded['config'].get('parallel') == config['parallel'])
            # Catalog's size_bytes is DISK size, not model RAM/VRAM usage.
        return models

    def memory(self, metadata, loading):
        self.check_file(metadata)
        config, _, _ = self.owned()
        rows = self.catalog()
        loaded = [i for m in rows for i in m.get('loaded_instances', [])]
        started = None
        if loading:
            if any(i.get('id') != metadata['id'] for i in loaded):
                raise ValueError('Unload the current model before loading another in this deployment')
            if not loaded:
                started = time.monotonic()
                self.command(['load', metadata['engine_key'], '--identifier', metadata['id'],
                              '--context-length', str(config['context_length']), '--parallel', str(config['parallel']),
                              '--ttl', str(config['keep_alive_seconds']), '--gpu', 'max', '--yes'])
            instances = [i for m in self.catalog() for i in m.get('loaded_instances', [])]
            current = next((i for i in instances if i.get('id') == metadata['id']), None)
            if not current or current.get('config', {}).get('context_length') != config['context_length'] or current['config'].get('parallel') != config['parallel']:
                raise ValueError('llmster did not confirm the requested context and concurrency')
        elif any(i.get('id') == metadata['id'] for i in loaded):
            self.control.request('/api/v1/models/unload', {'instance_id': metadata['id']})
            if any(i.get('id') == metadata['id'] for m in self.catalog() for i in m.get('loaded_instances', [])):
                raise ValueError('llmster did not confirm that the model was unloaded')
        return time.monotonic() - started if started is not None else None
