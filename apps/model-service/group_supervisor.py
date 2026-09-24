"""One original engine per live group; no scheduling, replacement or publication.

Packaged with group_network/group_mesh. The service gateway must consult ready()
for each request; a prior readiness result is not a transferable publication grant.
LinuxEngine keeps workers in the Fleet-owned process group, so Runner stop still
reclaims them when this supervisor is killed without running Python cleanup.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import ssl
import stat
import subprocess
import threading
import time

from group_network import PeerTopology, tls_contexts


def _read(directory, name):
    fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o400 or not 0 < info.st_size <= 16384:
            raise ValueError('Invalid sealed group credential file')
        value = stream.read(16385)
        if len(value) > 16384:
            raise ValueError('Group credential exceeds its bound')
        return value


def credentials(directory, identity, expected_topology):
    """Use Runner-delivered files only after checking this exact compiled plan."""
    directory = Path(directory)
    info = directory.lstat()
    names = {'group-peer.json', 'key.pem', 'certificate.pem', 'ca.pem'}
    if (not directory.is_absolute() or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o500
            or {p.name for p in directory.iterdir()} != names):
        raise ValueError('Group credentials must be a sealed instance directory')
    files = {name: _read(directory, name) for name in names}
    manifest = json.loads(files['group-peer.json'])
    if (not isinstance(manifest, dict) or set(manifest) != {'protocol', 'rank', 'topology', 'ca_sha256'}
            or type(manifest['protocol']) is not int or manifest['protocol'] != 1):
        raise ValueError('Invalid group credential manifest')
    topology = PeerTopology(manifest['topology'])
    rank = manifest['rank']
    member = topology.member(rank)
    expected = PeerTopology(expected_topology)
    if (topology.fingerprint != expected.fingerprint
            or identity.get('PANTHEON_FLEET_ID') != topology.document()['owner']
            or identity.get('PANTHEON_NODE_ID') != member['node_id']
            or identity.get('PANTHEON_INSTANCE_GENERATION') != str(member['generation'])):
        raise ValueError('Group credentials do not match the original node launch')
    ca = files['ca.pem'].decode('ascii')
    if (ca.count('-----BEGIN CERTIFICATE-----') != 1
            or hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca)).hexdigest() != manifest['ca_sha256']):
        raise ValueError('Group authority pin changed')
    # Both sides validate own/peer leaf identities during the full-mesh barrier,
    # before any engine is launched. Keys never enter return values or diagnostics.
    server, client = tls_contexts(*(directory / name for name in ('ca.pem', 'certificate.pem', 'key.pem')))
    return topology, rank, server, client


class Supervisor:
    """Checks live mesh independently of a bounded engine readiness probe.

    engine implements start(), poll(), stop(); the production LinuxEngine below
    owns only this Fleet process group. Engine stop failure is propagated and
    never treated as proof of released resources. A supervisor cannot be reused.
    """
    def __init__(self, mesh, engine, probe, *, startup_timeout=600, probe_interval=1,
                 probe_timeout=10, monitor_interval=.1):
        for value, low, high in ((startup_timeout, 0, 600), (probe_interval, 0, 30),
                                 (probe_timeout, 0, 60), (monitor_interval, 0, 1)):
            if type(value) not in (int, float) or not low < value <= high:
                raise ValueError('Supervisor deadlines must be bounded')
        if probe_interval >= probe_timeout:
            raise ValueError('Health probe interval must fit its deadline')
        self.mesh, self.engine, self.probe = mesh, engine, probe
        self.startup_timeout, self.probe_interval = startup_timeout, probe_interval
        self.probe_timeout, self.monitor_interval = probe_timeout, monitor_interval
        self._lock, self._engine_lock = threading.RLock(), threading.Lock()
        self._finished = threading.Event()
        self._entered = self._started = self._local_ready = self._stopped = False
        self._failure = self._stop_error = None
        self._last_probe = None

    def ready(self):
        with self._lock:
            if (not self._started or not self._local_ready or self._failure
                    or self._finished.is_set() or self.engine.poll() is not None
                    or time.monotonic() - self._last_probe > self.probe_timeout):
                return False
            try:
                return self.mesh.check(require_ready=True)
            except Exception:
                return False

    def _fail(self, reason):
        with self._lock:
            if not self._failure:
                self._failure = reason
            self._local_ready = False
        self.mesh.fail(reason)

    def _stop(self):
        with self._engine_lock:
            if self._started and not self._stopped:
                try:
                    self.engine.stop()
                    self._stopped = True
                except BaseException as error:
                    self._stop_error = error
                    self._fail('engine-cleanup-failed')

    def _monitor(self, cancelled, deadline):
        try:
            while not self._finished.wait(self.monitor_interval):
                with self._lock:
                    started, local, last = self._started, self._local_ready, self._last_probe
                if cancelled.is_set():
                    raise RuntimeError('cancelled')
                self.mesh.check()
                if started and self.engine.poll() is not None:
                    raise RuntimeError('engine-exited')
                if last is not None and time.monotonic() - last > self.probe_timeout:
                    raise RuntimeError('probe-expired')
                if time.monotonic() >= deadline and not (local and self.mesh.check(require_ready=True)):
                    raise RuntimeError('startup-expired')
        except Exception:
            # No engine/peer exception bodies (which may contain paths or secrets).
            self._fail('cancelled' if cancelled.is_set() else 'original-cohort-unavailable')
            self._stop()

    def run(self, cancelled):
        if self._entered:
            raise RuntimeError('A group supervisor cannot be restarted or reused')
        self._entered = True
        deadline = time.monotonic() + self.startup_timeout
        monitor = None
        try:
            with self.mesh:
                monitor = threading.Thread(target=self._monitor, args=(cancelled, deadline), daemon=True)
                monitor.start()
                try:
                    self.mesh.wait_connected()
                    with self._engine_lock:
                        if cancelled.is_set() or self._failure:
                            raise RuntimeError('Original group start cancelled')
                        self.mesh.transition('loading')
                        self._started = True
                        self.engine.start()
                    while not self._failure and not cancelled.is_set():
                        self.mesh.check()
                        with self._lock:
                            if self._last_probe is None:
                                self._last_probe = time.monotonic()
                        try:
                            self.probe()
                        except Exception:
                            if self._local_ready:
                                self._fail('local-engine-no-longer-ready')
                            else:
                                # A completed negative probe is normal during model
                                # loading; only a stuck probe or startup deadline fails.
                                with self._lock:
                                    self._last_probe = time.monotonic()
                        else:
                            with self._lock:
                                if not self._failure and not cancelled.is_set():
                                    self._last_probe = time.monotonic()
                                    if not self._local_ready:
                                        self.mesh.transition('ready')
                                        self._local_ready = True
                        cancelled.wait(self.probe_interval)
                finally:
                    self._fail('cancelled' if cancelled.is_set() else 'original-attempt-ended')
                    self._stop()
                    self._finished.set()
                    if monitor is not None:
                        monitor.join(self.probe_timeout + 1)
                        if monitor.is_alive():
                            raise RuntimeError('Engine cleanup monitor did not stop')
        finally:
            self._finished.set()
        if self._stop_error:
            raise RuntimeError('Original engine cleanup needs Fleet recovery') from self._stop_error
        if self._failure != 'cancelled':
            raise RuntimeError('Original model group failed')


class LinuxEngine:
    """Retain the Fleet-owned process group for descendants, including orphans.

    Caller installs SIGTERM/SIGINT handlers that set its cancellation event.
    If cooperative shutdown fails, killing the complete group also kills this
    supervisor. Fleet must observe original process-group exit before release.
    No detached session, daemon adoption, PID search by name or respawn is used.
    """
    def __init__(self, argv, env, *, stop_timeout=15):
        if (not Path('/proc/self/stat').is_file() or os.getpgrp() != os.getpid()
                or signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, signal.SIG_IGN)
                or type(stop_timeout) not in (int, float) or not 0 < stop_timeout <= 60):
            raise ValueError('Linux engine supervisor requires its own Fleet process group and stop handler')
        self.argv, self.env, self.stop_timeout = argv, env, stop_timeout
        self.process = None
        self.group = os.getpid()

    def start(self):
        if self.process is not None:
            raise RuntimeError('An original engine cannot be replaced')
        # Deliberately inherit the Fleet-owned group. Runner SIGKILL must still
        # stop engine workers when supervisor Python finally blocks cannot run.
        self.process = subprocess.Popen(self.argv, env=self.env, stdin=subprocess.DEVNULL)

    def poll(self):
        return self.process.poll() if self.process is not None else None

    def _workers(self):
        workers = []
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                row = path.read_text().rpartition(') ')[2].split()
                if int(path.parent.name) != self.group and int(row[2]) == self.group and row[0] not in {'Z', 'X'}:
                    workers.append(int(path.parent.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
        return workers

    def listener_identity(self, port):
        """Identify a loopback listener owned by this live engine's group.

        A healthy HTTP response alone could come from an older engine occupying
        the same port. Include socket inodes and PID birth times so a probe can
        reject a listener replaced while the HTTP request was in flight.
        """
        if self.process is None or self.process.poll() is not None:
            return None
        inodes = set()
        for name, loopback in (('tcp', '0100007F'),
                               ('tcp6', '00000000000000000000000001000000')):
            with Path('/proc/self/net', name).open() as source:
                content = source.read((16 << 20) + 1)
            if len(content) > 16 << 20:
                raise RuntimeError('Listener inventory exceeds its bound')
            for row in content.splitlines()[1:]:
                fields = row.split()
                address, number = fields[1].split(':')
                if fields[3] != '0A' or int(number, 16) != port:
                    continue
                if address != loopback:
                    return None  # The recipe never publishes the engine port.
                inodes.add(fields[9])
        if not inodes:
            return None
        owners = set()
        for pid in self._workers():
            directory = Path('/proc', str(pid))
            try:
                before = (directory / 'stat').read_text().rpartition(') ')[2].split()
                if int(before[2]) != self.group or before[0] in {'Z', 'X'}:
                    continue
                found = set()
                for fd in (directory / 'fd').iterdir():
                    try:
                        target = os.readlink(fd)
                    except FileNotFoundError:
                        continue
                    if target.startswith('socket:[') and target.endswith(']'):
                        inode = target[8:-1]
                        if inode in inodes:
                            found.add(inode)
                after = (directory / 'stat').read_text().rpartition(') ')[2].split()
                if before[19] != after[19] or int(after[2]) != self.group or after[0] in {'Z', 'X'}:
                    continue
                owners.update((inode, pid, before[19]) for inode in found)
            except (FileNotFoundError, ProcessLookupError):
                continue
        if {item[0] for item in owners} != inodes or self.process.poll() is not None:
            return None
        return tuple(sorted(owners))

    def stop(self):
        if self.process is None:
            return
        os.killpg(self.group, signal.SIGTERM)
        deadline = time.monotonic() + self.stop_timeout
        while time.monotonic() < deadline:
            self.process.poll()  # Reap the original child, even if its workers linger.
            if not self._workers():
                return
            time.sleep(.05)
        # Escalation is scoped to the original group, never to another rank.
        os.killpg(self.group, signal.SIGKILL)
        raise RuntimeError('Original engine group did not exit')  # Cannot normally execute.
