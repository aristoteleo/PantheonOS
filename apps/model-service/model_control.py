"""Durable model operations for owned engines, separate from inference grants.

Weights stay on the node. A job names a verified download, never a caller's file
path or arbitrary engine command. Interrupted operations require reconciliation.
"""
import http.client
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics
import threading
import time
from urllib.parse import urlsplit


def management_config(value, engine, scope, engines):
    keys = {'scope', 'recipe_id', 'context_length', 'parallel', 'keep_alive_seconds', 'memory_bytes'}
    if engine == 'sglang':
        keys.add('model_artifact_sha256')
    if not isinstance(value, dict) or set(value) - {'load_policy'} != keys:
        raise ValueError('An exact owned engine configuration is required')
    if not scope.startswith('model-') or value['scope'] != 'engine-' + scope.removeprefix('model-'):
        raise ValueError('Model management must refer to this deployment’s engine')
    if engines.recipe(value['recipe_id'])['engine'] != engine:
        raise ValueError('Managed engine recipe does not match its connector')
    if engine == 'sglang' and not re.fullmatch('[a-f0-9]{64}', str(value['model_artifact_sha256'])):
        raise ValueError('Managed SGLang requires a pinned model snapshot')
    for key, low, high in [('context_length', 512, 1048576), ('parallel', 1, 16),
                           ('keep_alive_seconds', 0, 86400), ('memory_bytes', 256 << 20, 1 << 50)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError('Invalid managed model budget or lifetime')
    policy = value.get('load_policy', 'manual')
    if not isinstance(policy, str) or policy not in {'manual', 'on_demand', 'warm', 'resident'}:
        raise ValueError('Invalid managed model loading policy')
    if ((policy == 'warm' and value['keep_alive_seconds'] < 1)
            or (policy in {'on_demand', 'resident'} and value['keep_alive_seconds'] != 0)
            or (engine == 'sglang' and (policy not in {'manual', 'resident'} or value['keep_alive_seconds'] != 0))
            or (engine == 'lmstudio' and (value['parallel'] != 1 or (policy == 'manual' and value['keep_alive_seconds'] < 1)))):
        raise ValueError('Invalid idle TTL for this model loading policy')
    return dict(value)


class ModelControl:
    def __init__(self, connector):
        self.connector = connector
        self.directory = connector.downloads().directory / 'models'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.mutex = threading.RLock()
        self.memory_lock = threading.RLock()
        self.owner = connector.module('artifacts').file_lock(self.directory / 'owner.lock')
        self.owner.__enter__()
        self.db = None
        self.worker = None
        try:
            self.db = sqlite3.connect(self.directory / 'models.sqlite3', check_same_thread=False)
            os.chmod(self.directory / 'models.sqlite3', 0o600)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT, state TEXT, phase TEXT, updated REAL, error TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, metadata TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS timing (id TEXT PRIMARY KEY, started REAL, elapsed REAL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS loads (id TEXT PRIMARY KEY, revision TEXT, samples TEXT, measured REAL)')
            self.started = {}
            self.db.execute("UPDATE jobs SET state='unknown',phase='Reconcile engine state before retrying',error='Model worker stopped before acknowledgement' WHERE state='running'")
            self.db.commit()
        except Exception:
            if self.db is not None:
                self.db.close()
            self.owner.__exit__(None, None, None)
            raise

    def config(self):
        value = self.connector.config or {}
        if not value.get('managed'):
            raise ValueError('Attached engines have no model management permission')
        if value['engine'] not in {'ollama', 'lmstudio', 'sglang'}:
            raise ValueError('This engine’s model management driver is not available yet')
        p = urlsplit(value['endpoint'])
        if p.scheme != 'http' or p.hostname != '127.0.0.1' or not p.port or p.path != '/v1':
            raise ValueError('Owned model management requires the Fleet-assigned loopback endpoint')
        return value['managed'], p.port

    def llmster(self):
        if self.connector.config.get('engine') == 'lmstudio':
            return self.connector.module('llmster_models').LLMsterModels(self)

    def inference_model(self, body, *, prepare=False, cancelled=lambda: False):
        config, _ = self.config()
        if set(body) & {'options', 'keep_alive', 'ttl', 'num_ctx', 'context_length', 'parallel', 'load_policy',
                        'num_gpu', 'gpu', 'model_path', 'load_config'}:
            raise ValueError('Inference parameters cannot change an owned model’s resource or lifetime settings')
        if body.get('n', 1) != 1:
            raise ValueError('Owned model requests generate one completion at a time')
        for key in ('max_tokens', 'max_completion_tokens'):
            if key in body and (type(body[key]) is not int or not 0 < body[key] <= config['context_length']):
                raise ValueError('Requested output exceeds the owned model context')
        model_id = body.get('model')
        if not isinstance(model_id, str):
            raise ValueError('An exact imported model id is required')
        if self.connector.config['engine'] == 'sglang':
            if model_id != 'fleet-snapshot-' + config['model_artifact_sha256']:
                raise ValueError('Request does not match the owned SGLang model')
            return {'id': model_id}
        with self.mutex:
            row = self.db.execute('SELECT metadata FROM models WHERE id=?', (model_id,)).fetchone()
        if not row:
            raise ValueError('Import this model into the deployment before using it')
        metadata = json.loads(row[0])
        if metadata['artifact']['size'] + (256 << 20) > config['memory_bytes']:
            raise ValueError('Weights and minimum engine overhead exceed the deployment budget')
        if driver := self.llmster():
            driver.check_file(metadata)
        else:
            current = next((m for m in self.request('/api/tags', timeout=5).get('models', []) if m.get('name') == model_id), None)
            if not current or current.get('digest') != metadata['manifest_digest']:
                raise ValueError('Owned model identity changed; reconcile it before inference')
        if prepare and config.get('load_policy', 'manual') != 'manual':
            self.ensure_loaded(metadata, cancelled)
            if cancelled():
                return metadata
        if driver:
            rows = driver.catalog()
            current = next((i for m in rows if m.get('key') == metadata['engine_key']
                            for i in m.get('loaded_instances', []) if i.get('id') == model_id), None)
            if not current or current.get('config', {}).get('context_length') != config['context_length'] or current['config'].get('parallel') != config['parallel']:
                raise ValueError('Load this exact model with the deployment settings before inference')
        return metadata

    def request(self, path, payload=None, *, method=None, timeout=180):
        _, port = self.config()
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            connection.request(method or ('POST' if body else 'GET'), path, body,
                               {'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            data = response.read((2 << 20) + 1)
            if response.status >= 400:
                raise ValueError(f'Owned engine rejected the model operation (HTTP {response.status})')
            if len(data) > 2 << 20:
                raise ValueError('Owned engine response exceeds the metadata limit')
            result = json.loads(data) if data else {}
            if result.get('error'):
                raise ValueError('Owned engine could not complete this model operation')
            return result
        finally:
            connection.close()

    def status(self):
        config, _ = self.config()
        if self.connector.config['engine'] == 'sglang':
            module = self.connector.module('snapshots')
            record = module.snapshot(self.connector.downloads().cache.root.parent, config['model_artifact_sha256'])
            if not record:
                raise ValueError('Owned SGLang snapshot is missing')
            model_id = 'fleet-snapshot-' + record['sha256']
            models = self.request('/v1/models', timeout=5).get('data', [])
            present = any(m.get('id') == model_id for m in models)
            return {'models': [dict(id=model_id, name=record['name'], loaded=present, inference_ready=present,
                artifact=dict(sha256=record['sha256'], revision=record['revision'], format='safetensors', size=record['weights_bytes']),
                context_length=config['context_length'], memory_bytes=None, gpu_memory_bytes=None,
                estimate=module.memory_estimate(record, config['context_length'], config['parallel']))], 'jobs': []}
        with self.mutex:
            models = [json.loads(r[0]) for r in self.db.execute('SELECT metadata FROM models ORDER BY id')]
            jobs = [dict(job_id=r[0], request=json.loads(r[1]), state=r[2], phase=r[3], updated_at=r[4], error=r[5])
                    for r in self.db.execute('SELECT * FROM jobs ORDER BY updated DESC LIMIT 128')]
            times = {r[0]: r[1:] for r in self.db.execute('SELECT id,started,elapsed FROM timing')}
            for job in jobs:
                timing = times.get(job['job_id'])
                job['started_at'] = timing[0] if timing else None
                # Never subtract wall clocks across processes or claim that a
                # recovered unknown operation has a measured end time.
                start = self.started.get(job['job_id'])
                job['elapsed_seconds'] = round(time.monotonic() - start, 3) if start is not None else (
                    timing[1] if timing and job['state'] != 'unknown' else None)
        try:
            if driver := self.llmster():
                return {'models': self.load_estimates(driver.observed(models)), 'jobs': jobs}
            observed = self.request('/api/ps', timeout=5).get('models', [])
        except (ValueError, OSError, http.client.HTTPException):
            # llmster can reject listLoaded while a model is being created. Job
            # progress is durable and remains useful even when live observation
            # fails. Unknown memory must never be presented as unloaded/free.
            for model in models:
                model.update(loaded=None, inference_ready=False, memory_bytes=None,
                             gpu_memory_bytes=None, expires_at=None)
            return {'models': self.load_estimates(models), 'jobs': jobs,
                    'observation_error': 'Model memory state is temporarily unavailable. Wait for the current operation or refresh.'}
        running = {m.get('name'): m for m in observed}
        for model in models:
            loaded = running.get(model['id'])
            model.update(loaded=bool(loaded), inference_ready=True, memory_bytes=loaded.get('size') if loaded else None,
                         gpu_memory_bytes=loaded.get('size_vram') if loaded else None,
                         expires_at=loaded.get('expires_at') if loaded else None)
        return {'models': self.load_estimates(models), 'jobs': jobs}

    def record_load(self, model_id, elapsed):
        """Only confirmed cold loads; at most 16 samples per imported model.

        Configuration includes the pinned engine, context, parallelism and
        resource reservation. A reconfiguration cannot inherit old timings.
        Warm no-op loads, failed/unknown operations and inference are excluded.
        """
        if type(elapsed) not in (int, float) or not 0 < elapsed <= 600 or not math.isfinite(elapsed):
            return
        revision, now = self.connector.revision, time.time()
        with self.mutex:
            if not self.db.execute('SELECT 1 FROM models WHERE id=?', (model_id,)).fetchone():
                return
            old = self.db.execute('SELECT revision,samples,measured FROM loads WHERE id=?', (model_id,)).fetchone()
            samples = json.loads(old[1]) if old and old[0] == revision else []
            samples = [s for s in samples if 0 <= now - s[0] <= 7 * 86400]
            samples = (samples + [[now, max(.001, round(elapsed * 1000, 3))]])[-16:]
            self.db.execute('INSERT OR REPLACE INTO loads VALUES (?,?,?,?)',
                            (model_id, revision, json.dumps(samples), now))
            self.db.commit()

    def load_estimates(self, models):
        revision, now = self.connector.revision, time.time()
        with self.mutex:
            samples = {r[0]: (json.loads(r[1]), r[2]) for r in self.db.execute(
                'SELECT id,samples,measured FROM loads WHERE revision=? AND measured>=? AND measured<=?',
                (revision, now - 7 * 86400, now))}
        for model in models:
            history, measured = samples.get(model['id'], ([], None))
            values = [value for stamp, value in history if 0 <= now - stamp <= 7 * 86400]
            model.update(cold_load_ms=statistics.median(values) if values else None,
                         load_samples=len(values), load_measured_at=measured if values else None)
        return models

    def submit(self, job_id, action, artifact_job_id='', model_id=''):
        config, _ = self.config()
        if action == 'load' and config.get('load_policy') == 'on_demand':
            raise ValueError('On-demand models load automatically for inference and unload after the request batch')
        if self.connector.config['engine'] == 'sglang':
            raise ValueError('This SGLang model is resident; stop the service to unload its engine')
        if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}', job_id):
            raise ValueError('Invalid model job id')
        if action not in {'import', 'load', 'unload'}:
            raise ValueError('Unsupported model operation')
        if action == 'import':
            if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}', artifact_job_id) or model_id:
                raise ValueError('Choose a verified artifact download to import')
        elif artifact_job_id or not re.fullmatch('fleet/[a-f0-9]{64}:latest', model_id):
            raise ValueError('Choose an exact model imported by this deployment')
        request = dict(action=action, artifact_job_id=artifact_job_id, model_id=model_id)
        encoded = json.dumps(request, sort_keys=True)
        # Shared admission lock: no load/unload can race inference admission,
        # endpoint reconfiguration or a graceful Fleet stop.
        with self.connector.lock, self.mutex:
            old = self.db.execute('SELECT request,state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if old:
                if old[0] != encoded:
                    raise ValueError('Model job id already refers to a different operation')
                return {'job_id': job_id}
            if self.connector.calls or self.connector.maintenance or self.connector.lifetime_pending or not self.connector.accepting:
                raise ValueError('Wait for active requests or model operations to finish')
            if self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] >= 128:
                raise ValueError('Model operation history is full; clear finished records first')
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', (job_id, encoded, 'running', 'Queued', time.time(), ''))
            self.db.execute('INSERT INTO timing VALUES (?,?,NULL)', (job_id, time.time()))
            self.db.commit()
            self.started[job_id] = time.monotonic()
            self.connector.maintenance = True
            self.worker = threading.Thread(target=self.run, args=(job_id, request), daemon=True)
            self.worker.start()
        return {'job_id': job_id}

    def update(self, job_id, state, phase, error=''):
        with self.mutex:
            self.db.execute('UPDATE jobs SET state=?,phase=?,updated=?,error=? WHERE id=?',
                            (state, phase, time.time(), error, job_id))
            if state != 'running' and job_id in self.started:
                self.db.execute('UPDATE timing SET elapsed=? WHERE id=?',
                                (round(time.monotonic() - self.started.pop(job_id), 3), job_id))
            self.db.commit()

    def import_model(self, job_id, artifact_job_id):
        downloads = self.connector.downloads()
        with downloads.mutex:
            row = downloads.db.execute('SELECT source,state FROM jobs WHERE id=?', (artifact_job_id,)).fetchone()
        if not row or row[1] != 'ready':
            raise ValueError('Complete and verify this download before importing it')
        source = json.loads(row[0])
        if source['format'].lower() != 'gguf':
            raise ValueError('This import driver accepts a single GGUF file')
        digest, size = source['sha256'], source['size']
        path = downloads.cache.root / digest
        # Never redownload as a side effect of an import or an App open.
        if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
            raise ValueError('The verified artifact is no longer available in this node cache')
        self.update(job_id, 'running', 'Verifying cached weights')
        self.connector.module('artifacts').ArtifactCache.verify(path, digest, size, threading.Event())
        with path.open('rb') as stream:
            if stream.read(4) != b'GGUF':
                raise ValueError('Artifact content is not GGUF')
        name = 'fleet/' + digest + ':latest'
        with self.mutex:
            if (not self.db.execute('SELECT 1 FROM models WHERE id=?', (name,)).fetchone()
                    and self.db.execute('SELECT COUNT(*) FROM models').fetchone()[0] >= 256):
                raise ValueError('This deployment already has 256 imported models')
        if driver := self.llmster():
            self.update(job_id, 'running', 'Importing verified weights')
            self.save_model(driver.imported(path, source, name))
            return
        _, port = self.config()
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        try:
            connection.request('HEAD', '/api/blobs/sha256:' + digest)
            response = connection.getresponse()
            response.read()
            if response.status == 404:
                self.update(job_id, 'running', 'Importing verified weights')
                connection.putrequest('POST', '/api/blobs/sha256:' + digest)
                connection.putheader('Content-Length', str(size))
                connection.putheader('Content-Type', 'application/octet-stream')
                connection.endheaders()
                with path.open('rb') as stream:
                    while block := stream.read(1 << 20):
                        connection.send(block)
                response = connection.getresponse()
                response.read(65536)
                if response.status not in {200, 201}:
                    raise ValueError('Owned engine could not import this verified blob')
            elif response.status != 200:
                raise ValueError('Owned engine could not inspect its model blob cache')
        finally:
            connection.close()
        self.update(job_id, 'running', 'Creating immutable model identity')
        config, _ = self.config()
        self.request('/api/create', {'model': name, 'files': {'model.gguf': 'sha256:' + digest},
                     'parameters': {'num_ctx': config['context_length']}, 'stream': False})
        rows = self.request('/api/tags').get('models', [])
        created = next((m for m in rows if m.get('name') == name), None)
        if not created or not re.fullmatch('[a-f0-9]{64}', created.get('digest', '')):
            raise ValueError('Owned engine did not confirm the imported model identity')
        details = created.get('details', {})
        details = {k: details[k] for k in ('format', 'family', 'parameter_size', 'quantization_level')
                   if isinstance(details.get(k), str) and len(details[k]) <= 256}
        metadata = dict(id=name, name=source['name'], artifact={k: source[k] for k in ('sha256', 'size', 'revision', 'format')},
                        manifest_digest=created['digest'], context_length=config['context_length'],
                        details=details)
        self.save_model(metadata)

    def save_model(self, metadata):
        with self.mutex:
            self.db.execute('INSERT OR REPLACE INTO models VALUES (?,?)', (metadata['id'], json.dumps(metadata)))
            self.db.commit()

    def memory(self, metadata, loading):
        """Mutate only the known owned model, under admission/maintenance fencing."""
        with self.memory_lock:
            config, _ = self.config()
            model_id = metadata['id']
            if loading and metadata['artifact']['size'] + (256 << 20) > config['memory_bytes']:
                raise ValueError('Weights and minimum engine overhead exceed this deployment’s memory budget')
            if driver := self.llmster():
                elapsed = driver.memory(metadata, loading)
                if loading and elapsed is not None:
                    self.record_load(model_id, elapsed)
                return
            current = next((m for m in self.request('/api/tags').get('models', []) if m.get('name') == model_id), None)
            if not current or current.get('digest') != metadata['manifest_digest']:
                raise ValueError('Engine model identity changed; reconcile it before loading or unloading')
            # On-demand keeps a model through its admitted batch. The connector
            # unloads only when all calls are gone; the engine cannot evict it
            # between an explicit preload and the actual inference request.
            ttl = -1 if config.get('load_policy') in {'resident', 'on_demand'} else config['keep_alive_seconds']
            cold = loading and ttl != 0 and model_id not in self.loaded_ids()
            started = time.monotonic()
            self.request('/api/generate', {'model': model_id, 'prompt': '', 'stream': False,
                         'keep_alive': ttl if loading else 0,
                         'options': {'num_ctx': config['context_length']}})
            present = model_id in self.loaded_ids()
            if (loading and ttl != 0) != present:
                raise ValueError('Engine did not confirm the requested model memory state')
            if cold:
                self.record_load(model_id, time.monotonic() - started)

    def loaded_ids(self):
        if driver := self.llmster():
            return {i['id'] for m in driver.catalog() for i in m.get('loaded_instances', [])}
        return {m['name'] for m in self.request('/api/ps', timeout=5).get('models', [])}

    def known_model(self, model_id):
        with self.mutex:
            row = self.db.execute('SELECT metadata FROM models WHERE id=?', (model_id,)).fetchone()
        if not row:
            raise ValueError('Engine has an unrecognized model; reconcile it before changing memory')
        return json.loads(row[0])

    def ensure_loaded(self, metadata, cancelled):
        # FIFO admission prevents different-model overlap; the mutex coalesces
        # parallel calls to the same model into one cold load. Cancellation and
        # Stop can still acquire connector.lock while the engine loads.
        with self.memory_lock:
            if cancelled():
                return
            with self.connector.lock:
                if any(c['model'] != metadata['id'] for c in self.connector.running_calls()):
                    raise ValueError('Another model still has active consumers')
            loaded = self.loaded_ids()
            for model_id in loaded - {metadata['id']}:
                if cancelled():
                    return
                self.memory(self.known_model(model_id), False)
            if metadata['id'] not in loaded and not cancelled():
                self.memory(metadata, True)

    def release_idle(self):
        # Caller has atomically fenced new admission with maintenance=True and
        # confirmed no active/queued calls. Never unload an attached/unknown
        # model or alter the engine's global configuration.
        config, _ = self.config()
        if config.get('load_policy') != 'on_demand':
            return
        with self.memory_lock:
            for model_id in self.loaded_ids():
                self.memory(self.known_model(model_id), False)

    def run(self, job_id, request):
        try:
            if request['action'] == 'import':
                self.import_model(job_id, request['artifact_job_id'])
            else:
                model_id = request['model_id']
                with self.mutex:
                    row = self.db.execute('SELECT metadata FROM models WHERE id=?', (model_id,)).fetchone()
                if not row:
                    raise ValueError('This model was not imported by this deployment')
                metadata = json.loads(row[0])
                config, _ = self.config()
                loading = request['action'] == 'load'
                if loading and metadata['artifact']['size'] + (256 << 20) > config['memory_bytes']:
                    raise ValueError('Weights and minimum engine overhead exceed this deployment’s memory budget')
                self.update(job_id, 'running', 'Loading model' if loading else 'Unloading model')
                self.memory(metadata, loading)
            self.update(job_id, 'succeeded', 'Completed')
        except ValueError as error:
            self.update(job_id, 'failed', 'Not completed', str(error)[:256])
        except Exception:
            # A transport failure may follow successful loading/import. Do not
            # blindly replay; the next status reads actual engine memory state.
            self.update(job_id, 'unknown', 'Reconcile engine state before retrying', 'Owned engine did not acknowledge this operation')
        finally:
            with self.connector.lock:
                self.connector.maintenance = False

    def forget(self, job_id):
        with self.mutex:
            self.db.execute("DELETE FROM jobs WHERE id=? AND state!='running'", (job_id,))
            self.db.execute('DELETE FROM timing WHERE id NOT IN (SELECT id FROM jobs)')
            self.db.commit()

    def close(self):
        if self.worker:
            self.worker.join()
        self.db.close()
        self.owner.__exit__(None, None, None)
