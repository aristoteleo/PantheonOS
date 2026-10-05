"""Owned local Fleet infrastructure for the CLI/Desktop compatibility launchers.

Uses the shipping Controller, NATS and Runner binaries supplied by the product
bundle. No Agent imports, Hub login, system daemon or ambient Fleet discovery.
App deployment/composition still uses the ordinary owner-side coordinator.
The caller must drain its Apps before closing this infrastructure.
"""
from contextlib import ExitStack
from dataclasses import dataclass, field
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import time

import httpx

from .registry_lock import registry_lock


@dataclass(frozen=True)
class LocalFleetBinaries:
    controller: Path
    broker: Path
    runner: Path

    def validate(self):
        for value in (self.controller, self.broker, self.runner):
            path = Path(value)
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError('Supply absolute executable paths from the local product bundle')


@dataclass(frozen=True)
class LocalFleetCoordinates:
    controller: str
    nats: str
    fleet_id: str
    node_id: str
    credentials: Path = field(repr=False)


def _private_secret(path):
    """Create once; never replace a corrupt identity with a different Fleet."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_hex(32) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size != 65):
            raise ValueError('Local Fleet identity must be an owner-private key file')
        value = stream.read(66).decode('ascii').strip()
    if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Invalid local Fleet identity; inspect the existing profile')
    return value


class LocalFleet:
    """One locally locked profile; readiness requires the actual node registry.

    Ports are selected per invocation and only bind loopback. A bind collision
    fails startup and drains owned children; it never attaches another service.
    Stale runtime files are not proof of a live node. Processes/credentials from
    the user's installed Fleet are neither inspected nor stopped.
    """
    def __init__(self, root, binaries, *, workspace, timeout=45):
        self.root = Path(root).expanduser().absolute()
        self.workspace = Path(workspace).expanduser().resolve()
        self.binaries, self.timeout = binaries, timeout
        self.coordinates = None
        self._children, self._stack = [], None
        self._renewal = None
        self._stop = asyncio.Event()

    def _check_children(self):
        for name, child in self._children:
            if child.returncode is not None:
                raise RuntimeError(f'Local Fleet {name} exited; inspect {self.root / (name + ".log")}')

    async def _spawn(self, name, command, env):
        log = self._stack.enter_context((self.root / (name + '.log')).open('ab'))
        pending = asyncio.create_task(asyncio.create_subprocess_exec(*map(str, command), cwd=self.workspace,
            env=env, stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log))
        interrupted = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                interrupted = True
        child = pending.result()
        self._children.append((name, child))
        if interrupted:
            raise asyncio.CancelledError

    async def _issue_owner(self, http, origin, nats, fleet_id, key):
        response = await http.post(origin + '/join', json={'key': key})
        response.raise_for_status()
        owner = response.json()
        if (owner.get('fleet_id') != fleet_id or owner.get('nats_url') != nats
                or not isinstance(owner.get('creds'), str) or not owner['creds']):
            raise RuntimeError('Local Fleet owner credentials are unavailable')
        # This claim is used only to schedule renewal of the trusted local
        # issuer's response. NATS still verifies the actual signed credential.
        try:
            jwt = owner['creds'].split('-----BEGIN NATS USER JWT-----\n', 1)[1].splitlines()[0]
            claim = jwt.split('.')[1]
            expires = json.loads(base64.urlsafe_b64decode(claim + '=' * (-len(claim) % 4)))['exp']
            if type(expires) is not int or expires <= time.time():
                raise ValueError
        except (ValueError, KeyError, IndexError):
            raise RuntimeError('Local Fleet issued an invalid credential lifetime') from None
        credentials = self.root / 'owner.creds'
        temporary = self.root / ('.owner-' + secrets.token_hex(8))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(owner['creds'])
            temporary.replace(credentials)
        finally:
            temporary.unlink(missing_ok=True)
        return expires

    async def _renew_owner(self, origin, nats, fleet_id, key, expires):
        async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
            while not self._stop.is_set():
                delay = min(300, max(.1, (expires - time.time()) / 2))
                try:
                    await asyncio.wait_for(self._stop.wait(), delay)
                except TimeoutError:
                    try:
                        expires = await self._issue_owner(http, origin, nats, fleet_id, key)
                    except (httpx.HTTPError, OSError):
                        if time.time() >= expires:
                            raise RuntimeError('Local Fleet owner credentials expired; restart this profile') from None
                        # Retry boundedly while the existing grant remains valid.
                        try:
                            await asyncio.wait_for(self._stop.wait(), min(5, max(.1, expires - time.time())))
                        except TimeoutError:
                            pass

    async def wait(self):
        """Let the product launcher observe failures without automatic restart."""
        if self.coordinates is None:
            raise RuntimeError('Local Fleet is not running')
        watchers = [asyncio.create_task(child.wait()) for _, child in self._children]
        try:
            done, _ = await asyncio.wait([*watchers, self._renewal], return_when=asyncio.FIRST_COMPLETED)
            if self._renewal in done:
                await self._renewal
            self._check_children()
        finally:
            for task in watchers:
                task.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)

    async def _wait(self, probe, deadline):
        while True:
            self._check_children()
            if await probe():
                self._check_children()
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('Local Fleet did not become ready; inspect its profile logs')
            await asyncio.sleep(.1)

    async def __aenter__(self):
        if os.name != 'posix':
            raise RuntimeError('Bundled local Fleet acceptance currently requires macOS or Linux')
        if self._stack is not None:
            raise RuntimeError('This local Fleet profile is already open')
        self.binaries.validate()
        if not self.workspace.is_dir():
            raise ValueError('Local Fleet workspace must be an existing directory')
        if self.root.is_symlink():
            raise ValueError('Local Fleet profile cannot be a symlink')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Local Fleet profile must be an owner-private directory')
        self._stack = ExitStack()
        self._stop = asyncio.Event()
        try:
            self._stack.enter_context(registry_lock(self.root / 'profile.lock', timeout=0))
            key = _private_secret(self.root / 'owner.key')
            service_key = _private_secret(self.root / 'service.key')
            fleet_id = 'f_' + hashlib.sha256(key.encode()).hexdigest()[:16]
            # Hold both ephemeral sockets while choosing distinct ports.
            with socket.socket() as ctl, socket.socket() as bus:
                ctl.bind(('127.0.0.1', 0))
                bus.bind(('127.0.0.1', 0))
                controller_addr = f'127.0.0.1:{ctl.getsockname()[1]}'
                broker_addr = f'127.0.0.1:{bus.getsockname()[1]}'
            origin, nats = 'http://' + controller_addr, 'nats://' + broker_addr
            node = self.root / 'node'
            node.mkdir(mode=0o700, exist_ok=True)
            (node / 'runtime.json').unlink(missing_ok=True)
            config = self.root / 'nats.conf'
            config.unlink(missing_ok=True)
            broker_pid = self.root / 'nats.pid'
            broker_pid.unlink(missing_ok=True)
            # Keep SDK/build executable resolution; drop ambient deployment
            # coordinates so a local profile cannot join the user's remote node.
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_'))}
            controller_env = {**env, 'FLEET_CONTROLLER_SERVICE_TOKEN': service_key}
            deadline = time.monotonic() + self.timeout
            await self._spawn('controller', [self.binaries.controller,
                '--addr', controller_addr, '--nats', nats, '--nats-listen', broker_addr,
                '--state-dir', self.root / 'controller', '--allowed-keys-file', self.root / 'owner.key',
                '--emit-nats-config', config, '--js-store-dir', self.root / 'broker',
                '--nats-pid-file', broker_pid], controller_env)
            async with httpx.AsyncClient(trust_env=False, timeout=1) as http:
                async def controller_ready():
                    try:
                        response = await http.get(origin + '/healthz')
                        return response.status_code == 200 and config.is_file()
                    except httpx.TransportError:
                        return False
                await self._wait(controller_ready, deadline)
                await self._spawn('broker', [self.binaries.broker, '-c', config, '--pid', broker_pid], env)
                async def broker_ready():
                    try:
                        _, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1',
                            int(broker_addr.rsplit(':', 1)[1])), 1)
                        writer.close()
                        await writer.wait_closed()
                        return True
                    except (OSError, TimeoutError):
                        return False
                await self._wait(broker_ready, deadline)
                await self._spawn('runner', [self.binaries.runner, 'up', '--controller', origin,
                    '--key-file', self.root / 'owner.key', '--state-dir', node,
                    '--workdir', self.workspace, '--share-dir', self.workspace,
                    '--name', 'Pantheon local', '--no-auto-update', '--no-capture-setup'], env)
                observed = {}
                async def node_ready():
                    try:
                        current = json.loads((node / 'runtime.json').read_text())
                        if current.get('fleet_id') != fleet_id or current.get('nats_url') != nats:
                            raise RuntimeError('Local Fleet published unexpected coordinates')
                        response = await http.get(origin + '/nodes', params={'fleet': fleet_id},
                            headers={'Authorization': 'Bearer ' + service_key})
                        response.raise_for_status()
                        rows = response.json()['nodes']
                        if any(row.get('node_id') == current.get('node_id') for row in rows):
                            observed.update(current)
                            return True
                    except (FileNotFoundError, json.JSONDecodeError, httpx.HTTPError):
                        pass
                    return False
                await self._wait(node_ready, deadline)
                # A Runner credential can answer commands but cannot issue them.
                # Mint a separate owner credential for the trusted composition
                # launcher; it must never become an Agent dependency credential.
                expires = await self._issue_owner(http, origin, nats, fleet_id, key)
            self.coordinates = LocalFleetCoordinates(origin, nats, fleet_id, observed['node_id'], self.root / 'owner.creds')
            self._renewal = asyncio.create_task(self._renew_owner(origin, nats, fleet_id, key, expires))
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def _close(self):
        self.coordinates = None
        errors = []
        self._stop.set()
        if self._renewal is not None:
            try:
                await self._renewal
            except Exception:
                errors.append('owner credential renewal failed')
            self._renewal = None
        for name, child in reversed(self._children):
            if child.returncode is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(child.wait(), 25)
                except TimeoutError:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                    await child.wait()
                    errors.append(name + ' exceeded shutdown grace period')
        self._children.clear()
        if self._stack is not None:
            self._stack.close()
            self._stack = None
        if errors:
            raise RuntimeError('Local Fleet shutdown did not complete cleanly: ' + ', '.join(errors))

    async def __aexit__(self, *exc):
        # Cleanup is owned once admitted, including repeated Ctrl-C during exit.
        cleanup = asyncio.create_task(self._close())
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
        if interrupted:
            raise asyncio.CancelledError
