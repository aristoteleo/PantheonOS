"""Own asynchronous GPU work independently of any client connection.

SGLang has no abort API. Cancel records intent, continues same-ID observation,
and discards the output only once GPU completion/failure is known. Lost creation
ACKs and missing upstream jobs remain unknown, retaining admission ownership.
"""
from http.client import HTTPException
import json
import socket
import threading
import time


POLL_SECONDS = 2
DEADLINE_SECONDS = 600


class VideoWorker:
    def __init__(self, jobs):
        self.jobs, self.c, self.store = jobs, jobs.connector, jobs.store
        self.journal = self.c.module('video_journal').VideoJournal(self.store)
        self.closed = threading.Event()

    def restore(self):
        # Called under Connector.lock before it becomes ready for new work.
        rows = self.store.db.execute('SELECT id,record FROM inference_jobs').fetchall()
        for job, raw in rows:
            record = json.loads(raw)
            if record.get('operation') != 'video' or not (
                    record['state'] in {'queued', 'running', 'cancelling'} or record.get('upstream_pending')):
                continue
            if not self.c.slots.acquire(blocking=False):
                raise ValueError('Saved video admission exceeds capacity; recovery required')
            call = {'video': True, 'cancelled': False, 'model': record['model'], 'state': 'running',
                    'started': time.monotonic(), 'queue_ms': record.get('queue_ms', 0)}
            self.c.calls[job] = call
            try:
                saved = self.journal.recovery(job, record['config_revision'])
                call['cancel_requested'] = saved['cancel_requested']
                if record['config_revision'] != self.c.revision:
                    self.hold(job, call, 'video_binding_changed')
                elif saved['action'] == 'not_submitted':
                    self.finish(job, call, 'cancelled', 'connector_restarted_before_submission')
                elif saved['action'] == 'unknown':
                    self.hold(job, call, 'video_submission_unknown')
                else:
                    self.start_observer(job, call)
            except (KeyError, ValueError):
                self.hold(job, call, 'video_recovery_required')

    def start_observer(self, job, call):
        call['parked'] = False
        thread = threading.Thread(target=self.run, args=(job, None, call), daemon=True)
        call['worker'] = thread
        thread.start()

    def hold(self, job, call, reason):
        # Unknown is not free capacity. No worker/network activity is needed
        # when an ID was never acknowledged; an owner must inspect the engine.
        with self.c.lock:
            call['parked'] = True
            self.jobs.transition(job, state='unknown', reason=reason, upstream_pending=True,
                                 upstream_cancel_confirmed=False, ended_at=None)
            self.c.activity.update(job, state='running', reason=reason, ended_at=None)

    def reconcile(self, job):
        with self.c.lock:
            record = self.jobs.status(job)
            if record.get('operation') != 'video':
                raise ValueError('Only video jobs support upstream reconciliation')
            call = self.c.calls.get(job)
            if call and call.get('parked'):
                # The old observer may still be closing its socket in finally.
                # Never let it close the replacement observer's connection.
                if (worker := call.get('worker')) and worker.is_alive():
                    raise ValueError('Video observer is still settling; inspect status and reconcile again')
                saved = self.journal.recovery(job, record['config_revision'])
                if record['config_revision'] != self.c.revision or saved['action'] not in {'observe', 'collect'}:
                    raise ValueError('No acknowledged video on the original engine to reconcile')
                self.start_observer(job, call)
            return self.jobs.status(job)

    def interrupt_io(self, call):
        connection = call.get('connection')
        if connection and connection.sock:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        response = call.get('upstream')
        if response:
            try:
                response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except (AttributeError, OSError):
                pass

    def cancel(self, job, reason='cancelled'):
        with self.c.lock:
            call = self.c.calls.get(job)
            if not call:
                return {'cancelled': False}
            record = self.jobs.status(job)
            saved = self.journal.cancel(job, record['config_revision'])
            call.update(cancel_requested=True, reason=reason)
            # Keep the creation connection alive to capture its ACK. Cancelling
            # observation would not stop GPU work and would lose that identity.
            if saved['phase'] in {'not_submitted', 'terminal'}:
                call['cancelled'] = True
                self.interrupt_io(call)
            self.jobs.transition(job, state='cancelling', reason=reason,
                                 upstream_pending=saved['outstanding'], upstream_cancel_confirmed=False)
            self.c.changed.notify_all()
            return {'cancelled': True, 'upstream_cancel_confirmed': False,
                    'upstream_pending': saved['outstanding']}

    def finish(self, job, call, state, reason, result=None):
        with self.c.lock:
            elapsed = round((time.time()-self.jobs.status(job)['created_at'])*1000)
            self.jobs.transition(job, state=state, reason=reason, result=result, upstream_pending=False,
                                 upstream_cancel_confirmed=False if call.get('cancel_requested') else None,
                                 ended_at=time.time(), elapsed_ms=elapsed)
            if state != 'succeeded':
                self.jobs.discard_outputs(job)
            self.c.activity.update(job, state='completed' if state == 'succeeded' else state,
                                   reason=reason, ended_at=time.time(), elapsed_ms=elapsed)
            if self.c.calls.pop(job, None) is call:
                if job in self.c.queue:
                    self.c.queue.remove(job)
                self.c.slots.release()
            self.c.idle.used()
            self.c.changed.notify_all()

    def run(self, job, plan, call):
        video = self.c.module('video')
        revision = self.jobs.status(job)['config_revision']
        try:
            if plan is not None:
                if self.c.admit(job, call, lambda: self.closed.is_set()):
                    self.finish(job, call, 'cancelled' if call['cancelled'] else 'failed',
                                call.get('reason', 'queue_timeout'))
                    return
                with self.c.lock:
                    if call['cancelled'] or self.closed.is_set():
                        self.finish(job, call, 'cancelled', 'cancelled_before_submission')
                        return
                    self.journal.begin(job, revision)
                    self.jobs.transition(job, state='running', started_at=time.time(),
                                         submitted=True, upstream_pending=True, queue_ms=call['queue_ms'])
                receipt = video.request(self.c, call, plan=plan)
                self.journal.observe(job, revision, receipt)
            delay = POLL_SECONDS
            while not self.closed.is_set():
                saved = self.journal.recovery(job, revision)
                if revision != self.c.revision:
                    self.hold(job, call, 'video_binding_changed')
                    return
                if saved['action'] == 'unknown':
                    self.hold(job, call, 'video_submission_unknown')
                    return
                if time.time()-self.jobs.status(job)['created_at'] > DEADLINE_SECONDS and not saved['cancel_requested']:
                    self.cancel(job, 'job_deadline')
                    saved = self.journal.recovery(job, revision)
                if saved['phase'] == 'terminal':
                    if saved['cancel_requested']:
                        self.finish(job, call, 'cancelled', call.get('reason', 'cancelled_after_upstream_finished'))
                    elif saved['upstream_state'] == 'failed':
                        self.finish(job, call, 'failed', 'upstream_video_failed')
                    else:
                        self.collect(job, call, saved)
                    return
                try:
                    receipt = video.request(self.c, call, upstream_id=saved['upstream_id'])
                    saved = self.journal.observe(job, revision, receipt)
                    with self.c.lock:
                        # Cancellation may arrive during observation; consult
                        # durable intent again rather than the stale GET snapshot.
                        saved = self.journal.read(job, revision)
                        self.jobs.transition(job, state='cancelling' if saved['cancel_requested'] else 'running',
                                             upstream_pending=saved['outstanding'], progress=saved['progress'],
                                             reason='upstream_cancel_unsupported' if saved['cancel_requested'] else '')
                        self.c.activity.update(job, state='running', reason='', ended_at=None)
                    delay = POLL_SECONDS
                    if saved['phase'] == 'terminal':
                        continue
                except (OSError, HTTPException, RuntimeError, ValueError, TypeError, KeyError) as error:
                    if self.closed.is_set():
                        return
                    reason = 'upstream_video_missing' if str(error) == 'upstream_http_404' else 'video_observation_unavailable'
                    if isinstance(error, (ValueError, TypeError, KeyError)) or reason == 'upstream_video_missing':
                        self.hold(job, call, reason)
                        return
                    # Retry only GET for this acknowledged ID; not creation.
                    self.jobs.transition(job, state='unknown', reason=reason, upstream_pending=True)
                    delay = min(max(POLL_SECONDS, delay*2), 30)
                self.closed.wait(delay)
        except Exception:
            if self.closed.is_set():
                return
            saved = self.journal.recovery(job, revision)
            if saved['outstanding']:
                self.hold(job, call, 'video_submission_unknown' if not saved['upstream_id'] else 'video_recovery_required')
            else:
                self.finish(job, call, 'cancelled' if saved['cancel_requested'] else 'failed', 'video_driver_error')
        finally:
            if connection := call.get('connection'):
                connection.close()

    def collect(self, job, call, saved):
        with self.c.lock:
            saved = self.journal.read(job, self.jobs.status(job)['config_revision'])
            if saved['cancel_requested']:
                self.finish(job, call, 'cancelled', call.get('reason', 'cancelled_after_upstream_finished'))
                return
            artifact = self.store.row(saved['output_id'])
            ready = artifact['state'] == 'ready'
            result = {'artifacts': [self.store.metadata(artifact)], 'usage': {}} if ready else None
        if result is None:
            result = self.c.module('video').download(self.c, call, saved['upstream_id'],
                {'output_id': saved['output_id'], 'output': {'max_size': self.c.module('video').OUTPUT_LIMIT}},
                self.store, 'inference-' + job)
        with self.c.lock:
            if call.get('cancel_requested'):
                self.finish(job, call, 'cancelled', call.get('reason', 'cancelled_after_upstream_finished'))
            else:
                self.finish(job, call, 'succeeded', '', result)

    def suspend(self):
        """Stop local observers for shutdown, never cancel the attached engine."""
        self.closed.set()
        with self.c.lock:
            calls = [call for call in self.c.calls.values() if call.get('video')]
            for call in calls:
                self.interrupt_io(call)
            self.c.changed.notify_all()
        for call in calls:
            if worker := call.get('worker'):
                worker.join(3)
