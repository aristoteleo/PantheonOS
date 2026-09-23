"""Durable asynchronous inference, sharing media's atomic lease ledger.

Payloads live only in admitted workers. The ledger retains a fingerprint and
bounded result, never credentials or provider errors. A process restart does not
replay queued/uncertain work. Closing the submitter's window does not cancel it.
"""
import hashlib
from http.client import HTTPException
import json
import re
import threading
import time


ACTIVE = {'queued', 'running', 'cancelling'}
LIMIT = 128
TIMEOUT_SECONDS = 600


def identity(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Invalid inference job identity')
    return value


class Jobs:
    def __init__(self, connector):
        self.connector = connector
        self.store = connector.media_store()
        cleanup = []
        removing = []
        with self.store.transaction():
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS inference_jobs (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, record TEXT NOT NULL)''')
            for job, fingerprint, raw in self.store.db.execute('SELECT * FROM inference_jobs').fetchall():
                record = json.loads(raw)
                if record['state'] in ACTIVE:
                    queued = record['state'] == 'queued'
                    record.update(state='cancelled' if queued else 'unknown',
                                  reason='connector_restarted_before_submission' if queued else 'connector_restarted',
                                  ended_at=time.time())
                    self.put(job, fingerprint, record)
                    self.store.db.execute('DELETE FROM leases WHERE job=?', ('inference-' + job,))
                if record.get('removing'):
                    removing.append(job)
                elif record['state'] != 'succeeded':
                    cleanup.append(job)
        for job in cleanup:
            self.discard_outputs(job)
        for job in removing:
            self.remove(job)

    def discard_outputs(self, job):
        # The worker has closed its stream before releasing partial outputs.
        # If unlink fails, the reservation survives and recovery tries again.
        owner = 'inference-' + job
        for artifact in self.store.outputs(owner):
            self.store.remove(artifact)

    def lookup(self, job):
        return self.store.db.execute('SELECT fingerprint,record FROM inference_jobs WHERE id=?', (identity(job),)).fetchone()

    def put(self, job, fingerprint, record):
        self.store.db.execute('INSERT OR REPLACE INTO inference_jobs VALUES (?,?,?)',
                             (job, fingerprint, json.dumps(record, allow_nan=False, separators=(',', ':'))))

    def status(self, job):
        with self.store.lock:
            old = self.lookup(job)
            if not old:
                raise KeyError('Unknown inference job')
            return json.loads(old[1])

    def list(self):
        with self.store.lock:
            records = [json.loads(r[0]) for r in self.store.db.execute('SELECT record FROM inference_jobs')]
        return [{k: v for k, v in r.items() if k != 'result'} for r in
                sorted(records, key=lambda r: r['created_at'], reverse=True)]

    def transition(self, job, **fields):
        with self.store.transaction():
            old = self.lookup(job)
            record = json.loads(old[1])
            record.update(fields)
            self.put(job, old[0], record)
            if record['state'] not in ACTIVE:
                if record['state'] == 'succeeded':
                    self.store.db.execute('''DELETE FROM leases WHERE job=? AND artifact NOT IN
                        (SELECT artifact FROM generated_media WHERE job=?)''', ('inference-' + job,) * 2)
                else:
                    self.store.db.execute('DELETE FROM leases WHERE job=?', ('inference-' + job,))
            return record

    def submit(self, body, revision):
        c = self.connector
        if not isinstance(body, dict):
            raise ValueError('Invalid typed inference request')
        job = identity(body.get('job_id'))
        encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > 128 * 1024:
            raise ValueError('Typed inference request exceeds 128 KiB')
        fingerprint = hashlib.sha256(revision.encode() + b'\n' + encoded).hexdigest()
        with c.lock:
            with self.store.lock:
                old = self.lookup(job)
                if old:
                    if old[0] != fingerprint:
                        raise ValueError('Job identity already used or cancelled; it was not resubmitted')
                    return json.loads(old[1])
            if (not c.config or c.revision != revision or not c.accepting or c.maintenance
                    or c.lifetime_pending or c.idle.blocked):
                raise ValueError('Service is unavailable for new inference jobs')
            plan = c.module('job_drivers').prepare(c.config, body)
            if len(c.queue) >= c.queue_capacity or not c.slots.acquire(blocking=False):
                raise ValueError('Model inference admission is full')
            call = {'cancelled': False, 'model': body['model'], 'state': 'queued', 'started': time.monotonic()}
            record = {'protocol': 1, 'job_id': job, 'model': body['model'], 'operation': body['operation'],
                      'config_revision': revision, 'state': 'queued', 'reason': '', 'created_at': time.time(),
                      'result': None}
            registered, durable, activity_registered = False, False, False
            try:
                with self.store.transaction():
                    if self.store.db.execute('SELECT COUNT(*) FROM inference_jobs').fetchone()[0] >= LIMIT:
                        raise ValueError('Inference history is full; remove a terminal job first')
                    if plan.get('multipart'):
                        c.module('transcription').validate_input(plan, self.store)
                    for artifact in plan['inputs']:
                        if self.store.row(artifact)['state'] != 'ready':
                            raise ValueError('Inference input is not ready')
                        self.store.db.execute('INSERT INTO leases VALUES (?,?)', (artifact, 'inference-' + job))
                    if plan.get('output'):
                        plan['output_id'] = self.store.reserve_output('inference-' + job, **plan['output'])['id']
                    self.put(job, fingerprint, record)
                durable = True
                if not c.activity.create(job, body['model'], body['operation'], revision):
                    self.transition(job, state='cancelled', reason='request_identity_already_recorded', ended_at=time.time())
                    raise ValueError('Request identity already recorded; nothing was submitted')
                activity_registered = True
                c.calls[job] = call
                c.queue.append(job)
                c.idle.used()
                worker = threading.Thread(target=self.run, args=(job, plan, call), daemon=True)
                worker.start()
                registered = True
                return record
            except BaseException:
                if durable and self.status(job)['state'] == 'queued':
                    self.transition(job, state='failed', reason='worker_not_started', ended_at=time.time())
                if durable:
                    self.discard_outputs(job)
                if activity_registered:
                    c.activity.update(job, state='failed', reason='worker_not_started', ended_at=time.time())
                raise
            finally:
                if not registered:
                    if c.calls.get(job) is call:
                        c.calls.pop(job, None)
                        if job in c.queue:
                            c.queue.remove(job)
                    c.slots.release()

    def run(self, job, plan, call):
        c = self.connector
        submitted, result, outcome, reason = False, None, 'failed', 'not_submitted'
        # A bounded wall-clock deadline, in addition to socket timeouts. Timer
        # uses the existing per-call socket shutdown to unblock quiet upstreams.
        deadline = threading.Timer(TIMEOUT_SECONDS, lambda: c.cancel(job, reason='job_deadline'))
        deadline.daemon = True
        deadline.start()
        try:
            if c.admit(job, call, lambda: False):
                return
            with c.lock:
                if call['cancelled']:
                    return
                self.transition(job, state='running', started_at=time.time(), queue_ms=call['queue_ms'])
            if (c.config or {}).get('managed'):
                c.model_control().inference_model(plan['payload'], prepare=True, cancelled=lambda: call['cancelled'])
            if call['cancelled']:
                return
            submitted = True
            if plan.get('driver') == 'diffusion_image':
                result = c.module('diffusion').image(c, plan, call, self.store, 'inference-' + job)
            else:
                response = (c.module('transcription').request(c, plan, call, self.store) if plan.get('multipart')
                            else c.inference_request(plan['path'], plan['payload'], call))
                with response:
                    with c.lock:
                        call['upstream'] = response
                    if call['cancelled']:
                        return
                    result = c.module('job_drivers').result(response, plan, store=self.store,
                        owner='inference-' + job, cancelled=lambda: call['cancelled'])
            outcome, reason = 'succeeded', ''
        except (OSError, HTTPException):
            outcome, reason = ('unknown' if submitted else 'failed'), 'connection_lost'
        except (ValueError, TypeError, KeyError):
            outcome, reason = ('unknown' if submitted else 'failed'), 'invalid_result' if submitted else 'invalid_model_state'
        except RuntimeError as error:
            # Only driver-owned numeric HTTP status codes become public reasons.
            reason = str(error)
            if not re.fullmatch('upstream_http_[0-9]{3}', reason):
                reason = 'driver_unavailable'
        except Exception:
            outcome, reason = ('unknown' if submitted else 'failed'), 'driver_error'
        finally:
            deadline.cancel()
            if connection := call.get('connection'):
                connection.close()
            release_idle = False
            try:
                with c.lock:
                    if call['cancelled']:
                        reason = call.get('reason', 'cancelled')
                        outcome = ('unknown' if submitted else 'failed') if reason == 'job_deadline' else 'cancelled'
                        result = None
                    elapsed_ms = round((time.monotonic() - call['started']) * 1000)
                    self.transition(job, state=outcome, reason=reason, result=result,
                                    submitted=submitted,
                                    upstream_cancel_confirmed=False if submitted and call['cancelled'] else None,
                                    ended_at=time.time(), elapsed_ms=elapsed_ms)
                    if outcome != 'succeeded':
                        self.discard_outputs(job)
                    c.activity.update(job, state='completed' if outcome == 'succeeded' else outcome,
                                      reason=reason, ended_at=time.time(), elapsed_ms=elapsed_ms,
                                      queue_ms=call.get('queue_ms'), usage=(result or {}).get('usage', {}))
            finally:
                with c.lock:
                    c.calls.pop(job, None)
                    if job in c.queue:
                        c.queue.remove(job)
                    c.idle.used()
                    if (not c.calls and not c.maintenance and
                            (c.config or {}).get('managed', {}).get('load_policy') == 'on_demand'):
                        c.maintenance = True
                        release_idle = True
                    c.changed.notify_all()
                c.slots.release()
                if release_idle:
                    c.finish_idle()

    def cancel(self, job):
        c = self.connector
        with c.lock:
            with self.store.transaction():
                old = self.lookup(job)
                if not old:
                    if self.store.db.execute('SELECT COUNT(*) FROM inference_jobs').fetchone()[0] >= LIMIT:
                        raise ValueError('Inference history is full')
                    record = {'protocol': 1, 'job_id': job, 'model': '', 'operation': '', 'config_revision': c.revision,
                              'state': 'cancelled', 'reason': 'cancelled_before_submission', 'created_at': time.time(),
                              'ended_at': time.time(), 'result': None}
                    self.put(job, '', record)
                    return record
                record = json.loads(old[1])
                if record['state'] not in ACTIVE:
                    return record
                record['state'] = 'cancelling'
                self.put(job, old[0], record)
            c.cancel(job)
            return record

    def remove(self, job):
        with self.connector.lock:
            owner = 'inference-' + job
            with self.store.transaction():
                old = self.lookup(job)
                if not old:
                    raise KeyError('Unknown inference job')
                if json.loads(old[1])['state'] in ACTIVE or job in self.connector.calls:
                    raise ValueError('Cancel active inference before removing its history')
                if self.store.db.execute('''SELECT 1 FROM leases WHERE job != ? AND artifact IN
                    (SELECT artifact FROM generated_media WHERE job=?) LIMIT 1''', (owner, owner)).fetchone():
                    raise ValueError('Output is still used by another model job')
                record = json.loads(old[1])
                record.update(removing=True, result=None)
                self.put(job, old[0], record)
                self.store.db.execute('DELETE FROM leases WHERE job=?', (owner,))
            self.discard_outputs(job)
            with self.store.transaction():
                self.store.db.execute('DELETE FROM inference_jobs WHERE id=?', (job,))
