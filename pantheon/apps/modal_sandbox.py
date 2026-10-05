"""Owned Modal container placement for a prepared ordinary App image.

No App-specific methods or inference credentials live here. Callers prepare an
immutable Modal image and explicit command/environment. A creation intent is
persisted before the SDK request; uncertain intents are never automatically
recreated. Recovery can terminate the exact tagged instance, not resume stdin.
"""
import asyncio
import hashlib
import json
import re
import sqlite3
import uuid

from .agent_execution_runner import ToolReceiptJournal, ExecutionRecoveryRequired, _encode, _identity
from pantheon.utils.owned_io import run_owned_io


async def _join(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class ModalSandboxOwner:
    """One creation identity with a local exclusive owner and bounded lifetime.

    SDK credentials remain on the controller. env is explicit, never inherited.
    Existing records admit only recover_stop(), never another Sandbox.create.
    A successful stop includes a terminal SDK poll and a durable saved receipt.
    Failed/unknown stop keeps ownership until explicit recovery or process exit.
    """
    def __init__(self, root, *, operation_id, app_name, image_id, argv, env=None,
                 timeout=900, cpu=1.0, memory=2048, gpu=None, modal_sdk=None):
        _identity(operation_id)
        if not isinstance(app_name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', app_name):
            raise ValueError('Supply an explicit Modal App name')
        if not isinstance(image_id, str) or not re.fullmatch(r'im-[A-Za-z0-9]+', image_id):
            raise ValueError('Build and pin a Modal image ID before creating an App container')
        if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(v, str) and v and '\x00' not in v for v in argv):
            raise ValueError('Supply a prepared command argument list')
        env = dict(env or {})
        if not all(isinstance(k, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k)
                   and isinstance(v, str) and '\x00' not in v for k, v in env.items()):
            raise ValueError('Invalid explicit App environment')
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            raise ValueError('Supply a finite container lifetime')
        if isinstance(cpu, bool) or not isinstance(cpu, (int, float)) or not 0 < cpu <= 64:
            raise ValueError('Invalid CPU bound')
        if type(memory) is not int or not 128 <= memory <= 262144:
            raise ValueError('Invalid memory bound')
        if gpu is not None and (not isinstance(gpu, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}', gpu)):
            raise ValueError('Invalid explicit GPU request')
        self.operation_id, self.app_name, self.image_id = operation_id, app_name, image_id
        self.argv, self.env = list(argv), env
        self.timeout, self.cpu, self.memory, self.gpu = timeout, cpu, memory, gpu
        self.nonce = uuid.uuid4().hex
        self.name = 'pantheon-app-' + self.nonce
        self.request = _encode({'app': app_name, 'image': image_id, 'argv': self.argv,
            'env_digest': hashlib.sha256(_encode(env, 256 * 1024).encode()).hexdigest(),
            'timeout': timeout, 'cpu': cpu, 'memory': memory, 'gpu': gpu}, 256 * 1024)
        self.tags = {'pantheon_operation': operation_id,
                     'pantheon_request': hashlib.sha256(self.request.encode()).hexdigest(),
                     'pantheon_owner': self.nonce}
        self.sdk = modal_sdk
        self.journal = ToolReceiptJournal(root, 'modal-app-owner')
        try:
            with sqlite3.connect(self.journal.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS modal_app (id TEXT PRIMARY KEY, '
                           'request TEXT NOT NULL, phase TEXT NOT NULL, backend_id TEXT, returncode INTEGER, '
                           'name TEXT NOT NULL, nonce TEXT NOT NULL)')
                self._initial_empty = db.execute('SELECT 1 FROM modal_app LIMIT 1').fetchone() is None
        except BaseException:
            self.journal.close()
            raise
        self.sandbox = None
        self.creation = self.stopping = None
        self._closed = False

    def _modal(self):
        if self.sdk is None:
            import modal
            self.sdk = modal
        return self.sdk

    def _admit(self):
        with sqlite3.connect(self.journal.path) as db:
            if db.execute('SELECT 1 FROM modal_app LIMIT 1').fetchone():
                raise ExecutionRecoveryRequired('Recover the saved container; do not repeat creation')
            db.execute("INSERT INTO modal_app VALUES (?,?,'prepared',NULL,NULL,?,?)",
                       (self.operation_id, self.request, self.name, self.nonce))

    def _save(self, phase, backend_id=None, returncode=None):
        with sqlite3.connect(self.journal.path) as db:
            result = db.execute('UPDATE modal_app SET phase=?,backend_id=COALESCE(?,backend_id),'
                'returncode=? WHERE id=? AND request=?',
                (phase, backend_id, returncode, self.operation_id, self.request))
            if result.rowcount != 1:
                raise ExecutionRecoveryRequired('The container creation record is missing or changed')

    def _record(self):
        with sqlite3.connect(self.journal.path) as db:
            rows = db.execute('SELECT id,request,phase,backend_id,returncode,name,nonce FROM modal_app').fetchall()
        if len(rows) != 1 or rows[0][:2] != (self.operation_id, self.request):
            raise ExecutionRecoveryRequired('Container ownership does not match this prepared request')
        self.name, self.nonce = rows[0][5:]
        self.tags['pantheon_owner'] = self.nonce
        return rows[0][2:5]

    async def _create(self):
        await run_owned_io(self._admit)
        sdk = self._modal()
        app = await sdk.App.lookup.aio(self.app_name, create_if_missing=True)
        image = sdk.Image.from_id(self.image_id)
        await run_owned_io(self._save, 'creating')
        self.sandbox = await sdk.Sandbox.create.aio(*self.argv, app=app, image=image,
            name=self.name, tags=self.tags, env=self.env, secrets=[], timeout=self.timeout,
            cpu=(self.cpu, self.cpu), memory=(self.memory, self.memory), gpu=self.gpu,
            pty=False, include_oidc_identity_token=False)
        await run_owned_io(self._save, 'created', self.sandbox.object_id)
        return self.sandbox

    async def start(self):
        if self._closed:
            raise RuntimeError('Container owner is closed')
        if self.creation is None:
            self.creation = asyncio.create_task(self._create())
            self.creation.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        try:
            return await asyncio.shield(self.creation)
        except asyncio.CancelledError:
            # A cancelled observer must not discard a late-created handle.
            await _join(asyncio.create_task(self.stop()))
            raise

    async def _resolve_for_stop(self):
        phase, identity, returncode = await run_owned_io(self._record)
        if phase == 'stopped':
            return None, identity
        if self.sandbox is not None:
            return self.sandbox, self.sandbox.object_id
        if phase == 'prepared':
            # The SDK creation call is preceded by the durable 'creating' phase.
            await run_owned_io(self._save, 'stopped')
            return None, None
        sdk = self._modal()
        try:
            sandbox = (await sdk.Sandbox.from_id.aio(identity) if identity else
                       await sdk.Sandbox.from_name.aio(self.app_name, self.name))
        except Exception as exc:
            # NotFound is not proof that a previously uncertain create failed.
            raise ExecutionRecoveryRequired('Cannot locate the saved container for confirmed stop') from exc
        if identity is None:
            tags = await sandbox.get_tags.aio()
            if any(tags.get(key) != value for key, value in self.tags.items()):
                raise ExecutionRecoveryRequired('Named container does not match the saved creation intent')
            await run_owned_io(self._save, 'created', sandbox.object_id)
        self.sandbox = sandbox
        return sandbox, sandbox.object_id

    async def _stop(self):
        if self.creation is None and self._initial_empty:
            self.journal.close()
            return {'backend_id': None, 'stopped': True}
        if self.creation is not None:
            # Creation failure may still have reached the server; resolve from
            # the durable name/ID instead of assuming there was no side effect.
            await asyncio.gather(self.creation, return_exceptions=True)
        sandbox, identity = await self._resolve_for_stop()
        if sandbox is not None:
            code = await sandbox.poll.aio()
            if code is None:
                try:
                    await sandbox.terminate.aio(wait=True)
                except Exception as exc:
                    code = await sandbox.poll.aio()
                    if type(code) is not int:
                        raise ExecutionRecoveryRequired('Modal termination outcome remains unknown') from exc
                code = await sandbox.poll.aio()
            if type(code) is not int:
                raise ExecutionRecoveryRequired('Modal has not confirmed container termination')
            await run_owned_io(self._save, 'stopped', identity, code)
        self._closed = True
        self.journal.close()
        return {'backend_id': identity, 'stopped': True}

    async def stop(self):
        self._closed = True
        if self.stopping is None:
            self.stopping = asyncio.create_task(self._stop())
        return await _join(self.stopping)

    async def recover_stop(self):
        """Explicitly reconcile the same saved creation intent, without restarting."""
        if self.creation is not None and not self.creation.done():
            raise RuntimeError('Creation is still owned; use stop to join it')
        if self.stopping is not None:
            if not self.stopping.done():
                return await _join(self.stopping)
            # An explicit recovery may retry a failed stop, never a create.
            self.stopping = None
        return await self.stop()
