"""Owner-only administrative release of unobservable attached video work.

No network calls, process termination, or inference retries occur here. The owner
must inspect/stop the external engine first. We record their attestation while
keeping the inference outcome unknown; this is never proof of GPU cancellation.
"""
import hashlib
import json
import secrets
import time


CONFIRMATION = 'I verified this video work has ended on the engine'


def recover(connector, job_id, config_revision, action='inspect', ticket='', confirmation=''):
    if action not in {'inspect', 'release'}:
        raise ValueError('Unsupported video recovery action')
    with connector.lock:
        jobs = connector.inference_jobs()
        try:
            record = jobs.status(job_id)
        except KeyError as error:
            raise ValueError('Video job is unavailable on the original service') from error
        if (record.get('operation') != 'video' or config_revision != connector.revision
                or record['config_revision'] != config_revision):
            raise ValueError('Inspect the original video service configuration')
        journal = jobs.videos.journal
        saved = journal.read(job_id, config_revision)
        if action == 'release' and (confirmation != CONFIRMATION or not isinstance(ticket, str) or len(ticket) != 64):
            raise ValueError('Explicit owner confirmation and inspected ticket required')
        # If the response was lost after the durable commit, repeating exactly
        # that owner action may finish local cleanup but never release twice.
        repeated = action == 'release' and saved['phase'] == 'owner_released'
        if repeated:
            if not secrets.compare_digest(ticket, saved['release_ticket']):
                raise ValueError('Recovery ticket does not match the recorded release')
        else:
            call = connector.calls.get(job_id)
            worker = (call or {}).get('worker')
            if (not call or not call.get('parked') or (worker and worker.is_alive())
                    or not record.get('upstream_pending') or not saved['outstanding']):
                raise ValueError('Recovery requires an outstanding video with a stopped observer')
            snapshot = json.dumps([connector.run_id, job_id, saved], sort_keys=True,
                                  separators=(',', ':'), allow_nan=False).encode()
            expected = hashlib.sha256(snapshot).hexdigest()
            if action == 'inspect':
                return {'protocol': 1, 'job_id': job_id, 'config_revision': config_revision,
                        'ticket': expected, 'upstream_id': saved['upstream_id'],
                        'upstream_state': saved['upstream_state'],
                        'can_observe': saved['phase'] == 'observing',
                        'confirmation': CONFIRMATION}
            if not secrets.compare_digest(ticket, expected):
                raise ValueError('Video state changed; inspect recovery again')
            now = time.time()
            with jobs.store.transaction():
                journal.stage_owner_release(job_id, config_revision, ticket)
                jobs.stage_transition(job_id, state='unknown', reason='owner_released_reservation',
                    upstream_pending=False, upstream_cancel_confirmed=False, ended_at=now,
                    elapsed_ms=round((now-record['created_at'])*1000), result=None,
                    resolution={'kind': 'owner_release', 'at': now, 'verified_by_engine': False})
        # Commit before freeing admission. A crash before/after this point cannot
        # resurrect a worker or submit another generation on restart.
        if connector.calls.pop(job_id, None) is not None:
            if job_id in connector.queue:
                connector.queue.remove(job_id)
            connector.slots.release()
        connector.idle.used()
        connector.changed.notify_all()
        jobs.discard_outputs(job_id)
        record = jobs.status(job_id)
        connector.activity.update(job_id, state='unknown', reason=record['reason'],
                                  ended_at=record['ended_at'], elapsed_ms=record['elapsed_ms'])
        return record
