"""Durable, owner-selected warm replicas within existing engine reservations.

One imported model per owned resident engine. Multiple deployments/nodes can be
members of a route's warm pool without accumulating unbudgeted models in a single
engine. No background inference, downloads, daemon starts or automatic retries.
"""
import time
import uuid


class Preload:
    def __init__(self, control):
        self.control = control
        control.db.execute('CREATE TABLE IF NOT EXISTS preload (id INTEGER PRIMARY KEY CHECK(id=1), model TEXT, revision INTEGER, job TEXT, configuration TEXT)')
        control.db.execute("INSERT OR IGNORE INTO preload VALUES (1,'',0,'','')")
        control.db.commit()

    def snapshot(self):
        c = self.control
        with c.mutex:
            model, revision, job, configuration = c.db.execute('SELECT model,revision,job,configuration FROM preload WHERE id=1').fetchone()
            state = c.db.execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone() if job else None
        return dict(protocol=1, model_id=model, revision=revision, job_id=job, config_revision=configuration,
                    state=(state[0] if state else 'unknown') if model else 'disabled')

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

    def guard(self, model_id):
        p = self.snapshot()
        if p['model_id']:
            if p['state'] != 'succeeded' or p['config_revision'] != self.control.connector.revision:
                raise ValueError('Preloading is not confirmed; inspect its job and explicitly retry')
            if p['model_id'] != model_id:
                raise ValueError('This warm replica serves its preloaded model; choose another service or change the preload selection')

    def project(self, result):
        p = self.snapshot()
        selected = next((m for m in result['models'] if m['id'] == p['model_id']), None)
        p['ready'] = bool(p['model_id'] and p['state'] == 'succeeded' and selected
                          and p['config_revision'] == self.control.connector.revision
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
        # Synchronous owner restoration, not a detached worker or inference.
        # run() records uncertain results and never retries them automatically.
        c.run(job_id, request, release_admission=False)
