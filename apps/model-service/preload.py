"""Durable, owner-selected warm replicas within existing engine reservations.

One imported model per owned resident engine. Multiple deployments/nodes can be
members of a route's warm pool without accumulating unbudgeted models in a single
engine. Explicit Ollama preload includes a fixed, two-token compute warmup;
metadata never generates, downloads, starts daemons or retries uncertain jobs.
"""
import time
import uuid


class Preload:
    def __init__(self, control):
        self.control = control
        control.db.execute('CREATE TABLE IF NOT EXISTS preload (id INTEGER PRIMARY KEY CHECK(id=1), model TEXT, revision INTEGER, job TEXT, configuration TEXT)')
        control.db.execute("INSERT OR IGNORE INTO preload VALUES (1,'',0,'','')")
        # A separate table preserves rollback compatibility with the 0.1.10
        # five-column selection record. Old receipts are not compute-ready.
        control.db.execute('CREATE TABLE IF NOT EXISTS preload_warmup (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER)')
        control.db.execute('INSERT OR IGNORE INTO preload_warmup VALUES (1,0)')
        control.db.commit()

    def snapshot(self):
        c = self.control
        with c.mutex:
            model, revision, job, configuration = c.db.execute('SELECT model,revision,job,configuration FROM preload WHERE id=1').fetchone()
            state = c.db.execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone() if job else None
            warmup, = c.db.execute('SELECT version FROM preload_warmup WHERE id=1').fetchone()
        return dict(protocol=1, model_id=model, revision=revision, job_id=job, config_revision=configuration,
                    warmup_version=warmup,
                    state=(state[0] if state else 'unknown') if model else 'disabled')

    def compute_ready(self, snapshot):
        return self.control.connector.config.get('engine') != 'ollama' or snapshot['warmup_version'] == 1

    def warm(self, job_id, model_id):
        """One bounded local compute probe, inside the owner job/admission fence.

        Empty-prompt loading does not exercise prefill and decode kernels on
        fresh CUDA runners. Never warm from discovery or ordinary inference.
        The fixed public prompt is unrelated to user data; output is discarded.
        Transport failures retain the existing unknown/no-replay semantics.
        """
        c = self.control
        if c.connector.config.get('engine') != 'ollama':
            return
        c.update(job_id, 'running', 'Warming inference kernels')
        result = c.request('/v1/chat/completions', {'model': model_id,
            'messages': [{'role': 'user', 'content': 'Warm up the model. Respond with two short words.'}],
            'stream': False, 'temperature': 0, 'max_tokens': 2}, timeout=180)
        usage, choices = result.get('usage') or {}, result.get('choices') or []
        count = usage.get('completion_tokens')
        if (type(count) is not int or not 0 < count <= 2 or len(choices) != 1
                or choices[0].get('finish_reason') not in {'stop', 'length'}):
            raise ValueError('Owned engine did not confirm bounded compute warmup')
        with c.mutex:
            c.db.execute('UPDATE preload_warmup SET version=1 WHERE id=1')

    def select(self, action, model_id, revision, job_id):
        """Caller holds admission + DB locks; commit with the durable job."""
        c = self.control
        previous = self.snapshot()
        if type(revision) is not int or revision != previous['revision']:
            raise ValueError('Preload selection changed; refresh before changing it')
        if action == 'preload':
            config, _ = c.config()
            if config.get('load_policy') != 'resident':
                raise ValueError('Preloading requires a resident service with a reserved memory budget')
            metadata = c.known_model(model_id)
            if metadata['artifact']['size'] + (256 << 20) > config['memory_bytes']:
                raise ValueError('Preloaded weights and engine overhead exceed the deployment budget')
        elif previous['model_id'] != model_id:
            raise ValueError('Refresh and choose the currently preloaded model to disable preloading')
        confirmed = previous['config_revision'] if (action == 'preload' and previous['model_id'] == model_id
            and previous['state'] == 'succeeded' and previous['config_revision'] == c.connector.revision) else ''
        c.db.execute('UPDATE preload SET model=?,revision=?,job=?,configuration=? WHERE id=1',
                     (model_id if action == 'preload' else '', revision + 1, job_id, confirmed))
        c.db.execute('UPDATE preload_warmup SET version=? WHERE id=1',
                     (previous['warmup_version'] if confirmed else 0,))

    def guard(self, model_id):
        p = self.snapshot()
        if p['model_id']:
            if (p['state'] != 'succeeded' or p['config_revision'] != self.control.connector.revision
                    or not self.compute_ready(p)):
                raise ValueError('Preloading is not confirmed; inspect its job and explicitly retry')
            if p['model_id'] != model_id:
                raise ValueError('This warm replica serves its preloaded model; choose another service or change the preload selection')

    def project(self, result):
        p = self.snapshot()
        selected = next((m for m in result['models'] if m['id'] == p['model_id']), None)
        p['ready'] = bool(p['model_id'] and p['state'] == 'succeeded' and selected
                          and p['config_revision'] == self.control.connector.revision
                          and self.compute_ready(p)
                          and selected.get('loaded') is True and selected.get('inference_ready'))
        if p['model_id']:
            for model in result['models']:
                model['inference_ready'] = bool(p['ready'] and model['id'] == p['model_id'])
        return {**result, 'preload': p}

    def restore(self):
        """Owner configure/resume already fenced admission and verified binding.

        A completed preload may be restored after a stopped engine is restarted.
        Failed/interrupted jobs are visible and require a new explicit owner job;
        repeated recovery never replays an uncertain load.
        """
        c = self.control
        p = self.snapshot()
        if not p['model_id'] or p['state'] != 'succeeded':
            return
        config, _ = c.config()
        if config.get('load_policy') != 'resident':
            raise ValueError('Disable preloading before changing the resident loading policy')
        if c.known_model(p['model_id'])['artifact']['size'] + (256 << 20) > config['memory_bytes']:
            raise ValueError('Preloaded model exceeds this deployment’s memory budget')
        observed = c.status()
        if observed.get('observation_error'):
            raise ValueError('Observe the owned engine before restoring preloaded memory')
        if observed['preload']['ready'] and p['config_revision'] == c.connector.revision:
            return
        job_id = 'preload-restore-' + uuid.uuid4().hex
        with c.mutex:
            if c.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] >= 128:
                raise ValueError('Clear finished model operations before restoring the warm replica')
            import json
            request = dict(action='preload', artifact_job_id='', model_id=p['model_id'], restore=True,
                           reload=p['config_revision'] != c.connector.revision)
            c.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',
                         (job_id, json.dumps(request, sort_keys=True), 'running', 'Checking preloaded model', time.time(), ''))
            c.db.execute('INSERT INTO timing VALUES (?,?,NULL)', (job_id, time.time()))
            c.db.execute('UPDATE preload SET job=? WHERE id=1', (job_id,))
            c.db.commit()
            c.started[job_id] = time.monotonic()
        # Synchronous owner restoration; any compute warmup stays in this job.
        # run() records uncertain results and never retries them automatically.
        c.run(job_id, request, release_admission=False)
