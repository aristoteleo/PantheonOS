"""Durable admission handshake for a Fleet-owned engine's idle shutdown.

This module never stops a process or releases a reservation. Fleet must verify
the exact engine binding, obtain this fence, confirm exit itself, and verify the
new binding before resuming. Inference credentials cannot call these methods.
"""
import hashlib
import json
import os
import re
import time
from urllib.parse import urlsplit


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    # Persist the directory entry as well as its contents before acknowledging
    # the fence. Windows does not support opening a directory this way.
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class IdleGuard:
    def __init__(self, connector):
        self.connector = connector
        self.path = connector.data / 'engine-idle.json'
        self.record = None
        self.checking = self.resuming = False
        self.last_use = time.monotonic()
        self.use_epoch = 0
        if self.path.exists():
            if self.path.stat().st_size > 4096:
                raise ValueError('Invalid saved engine idle state')
            self.record = json.loads(self.path.read_text())
            r = self.record
            if (not isinstance(r, dict) or r.get('protocol') != 1
                    or r.get('phase') not in {'fenced', 'resuming', 'resumed', 'stopped', 'reset'}
                    or type(r.get('idle_epoch')) is not int or r['idle_epoch'] < 1
                    or not re.fullmatch('[A-Za-z0-9_-]{1,100}', str(r.get('suspend_id', '')))
                    or not re.fullmatch('[a-f0-9]{64}', str(r.get('config_revision', '')))
                    or type(r.get('idle_seconds')) is not int or not 1 <= r['idle_seconds'] <= 86400
                    or (r['phase'] in {'resuming', 'resumed'} and
                        not re.fullmatch('[a-f0-9]{64}', str(r.get('resume_revision', ''))))):
                raise ValueError('Invalid saved engine idle state')
        if self.blocked or (self.record and self.record['phase'] == 'reset'):
            connector.accepting = False

    @property
    def blocked(self):
        return bool(self.record and self.record['phase'] in {'fenced', 'resuming', 'stopped'})

    def used(self):
        # Caller holds the shared admission lock. Metadata polling never calls
        # this: an open status panel must not keep an idle engine alive.
        self.last_use = time.monotonic()
        self.use_epoch += 1

    def status(self):
        return {'protocol': 1, 'phase': self.record['phase'] if self.record else 'active',
                'idle_epoch': self.record['idle_epoch'] if self.record else 0,
                'suspend_id': self.record['suspend_id'] if self.record else None,
                'config_revision': self.record['config_revision'] if self.record else None,
                'resume_revision': self.record.get('resume_revision') if self.record else None,
                'admission_fenced': self.blocked,
                'idle_seconds': max(0, round(time.monotonic() - self.last_use, 3))}

    def save(self, record):
        atomic_json(self.path, record)
        self.record = record
        self.connector._probe = None

    def result(self, status, *, safe=False):
        return {'status': status, 'safe_to_stop': safe, 'idle': self.status(),
                'config_revision': self.connector.revision,
                'accepting': self.connector.accepting and not self.connector.maintenance
                    and not self.connector.lifetime_pending}

    def drain(self, suspend_id, config_revision, idle_seconds, idle_epoch):
        c = self.connector
        if (not isinstance(suspend_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', suspend_id)
                or type(idle_seconds) is not int or not 1 <= idle_seconds <= 86400
                or type(idle_epoch) is not int or idle_epoch < 0):
            raise ValueError('Invalid engine idle request')
        with c.lock:
            if config_revision != c.revision:
                raise ValueError('Configuration changed before idle shutdown')
            if self.record and suspend_id == self.record['suspend_id']:
                r = self.record
                if (r['config_revision'] != config_revision or r['idle_seconds'] != idle_seconds
                        or r['idle_epoch'] != idle_epoch + 1):
                    raise ValueError('Idle request identity already refers to a different operation')
                # A lost acknowledgement may be retried. A completed wake or
                # explicit Stop can never be turned back into a shutdown ACK.
                return self.result(r['phase'], safe=r['phase'] == 'fenced')
            if idle_epoch != self.status()['idle_epoch']:
                raise ValueError('Idle generation changed; refresh before shutdown')
            managed = (c.config or {}).get('managed', {})
            if (managed.get('load_policy') not in {'on_demand', 'warm'}
                    or c.config.get('engine') not in {'ollama', 'lmstudio'}):
                raise ValueError('Automatic idle shutdown requires an owned on-demand or warm engine')
            if managed['load_policy'] == 'warm' and idle_seconds < managed['keep_alive_seconds']:
                raise ValueError('Engine idle timeout must preserve the model warm lifetime')
            if self.blocked or self.checking or self.resuming:
                return self.result('waiting')
            if not c.accepting:
                return self.result('stopped')
            if c.calls or c.maintenance or c.lifetime_pending or c.lifetime_error:
                return self.result('busy')
            if time.monotonic() - self.last_use < idle_seconds:
                return self.result('waiting')
            epoch, use_epoch = c.drain_epoch, self.use_epoch
            self.checking = True
        try:
            # Do not hold the admission/cancellation lock or reject incoming
            # calls during an engine metadata probe. Any new use invalidates
            # this observation even if that request finishes before we return.
            empty = c.model_control().empty_for_idle()
            with c.lock:
                if (epoch != c.drain_epoch or config_revision != c.revision or not c.accepting
                        or use_epoch != self.use_epoch or c.calls or c.maintenance
                        or c.lifetime_pending or c.lifetime_error):
                    return self.result('busy')
                if not empty:
                    return self.result('models_loaded_or_unknown')
                self.save({'protocol': 1, 'phase': 'fenced', 'suspend_id': suspend_id,
                           'config_revision': config_revision, 'idle_seconds': idle_seconds,
                           'idle_epoch': idle_epoch + 1})
                c.accepting = False
                c.changed.notify_all()
                return self.result('fenced', safe=True)
        finally:
            with c.lock:
                self.checking = False

    def stopped(self):
        # Called by explicit drain, including when it races a wake. Persist the
        # revocation so a delayed wake cannot undo Stop after a connector crash.
        if self.blocked and self.record['phase'] != 'stopped':
            self.save({**self.record, 'phase': 'stopped'})

    def reset(self, suspend_id, config_revision):
        """Explicit owner recovery only; this does not resume admission."""
        c = self.connector
        with c.lock:
            if (not self.record or self.record['phase'] != 'stopped'
                    or suspend_id != self.record['suspend_id'] or config_revision != c.revision):
                raise ValueError('Verify the stopped engine idle operation before recovery')
            if self.resuming or self.checking or c.calls or c.maintenance:
                raise ValueError('Wait for the current operation before recovery')
            self.save({**self.record, 'phase': 'reset'})
            return self.result('reset')

    def recovered(self):
        # Normal owner recovery is a separate explicit action after reset.
        # Persist completion so another restart does not resurrect the fence.
        if self.record and self.record['phase'] == 'reset':
            self.save({**self.record, 'phase': 'resumed', 'resume_revision': self.connector.revision})

    def resume(self, suspend_id, config_revision, config, managed):
        c = self.connector
        target = c.configuration(config, managed)
        target_revision = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()
        with c.lock:
            r = self.record
            if (not r or suspend_id != r['suspend_id'] or config_revision != r['config_revision']
                    or r['phase'] not in {'fenced', 'resuming', 'resumed'}):
                raise ValueError('Verify the exact idle operation before waking the engine')
            if r['phase'] == 'resumed':
                if target_revision != r['resume_revision'] or c.revision != target_revision:
                    raise ValueError('Idle wake already refers to a different configuration')
                return self.result('resumed')  # Observation only, never re-open a drained service.
            if self.resuming or self.checking or c.calls or c.maintenance:
                return self.result('waiting')
            if (c.revision not in {config_revision, r.get('resume_revision')}
                    or (r.get('resume_revision') and r['resume_revision'] != target_revision)
                    or {k: v for k, v in target.items() if k != 'endpoint'} !=
                       {k: v for k, v in c.config.items() if k != 'endpoint'}):
                raise ValueError('Idle wake can only rebind the same owned engine configuration')
            endpoint = urlsplit(target['endpoint'])
            if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1'
                    or not endpoint.port or endpoint.path != '/v1'):
                raise ValueError('Idle wake requires a verified Fleet loopback endpoint')
            self.save({**r, 'phase': 'resuming', 'resume_revision': target_revision})
            self.resuming = True
            c.accepting = False
            c.maintenance = True
            epoch = c.drain_epoch
        try:
            with c.lock:
                if epoch != c.drain_epoch or self.record['phase'] != 'resuming':
                    raise ValueError('Service was stopped before idle wake')
                c.store_configuration(target)
            if not c.finish_idle():
                raise ValueError(c.lifetime_error)
            with c.lock:
                if epoch != c.drain_epoch or self.record['phase'] != 'resuming':
                    raise ValueError('Service was stopped during idle wake')
                self.save({**self.record, 'phase': 'resumed'})
                self.used()
                c.accepting = True
                c.changed.notify_all()
                return self.result('resumed')
        finally:
            with c.lock:
                self.resuming = False
                c.maintenance = False
                c.changed.notify_all()
