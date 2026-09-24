"""Durable, digest-checked code inputs and deterministic group rank packages.

The store belongs on the Agent's durable workspace, not its image or temporary
directory. Missing/corrupt original input fails closed; no current-code fallback.
Only small code and public model receipts are stored here, never model weights,
credentials, installed engines or process state.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

from .group_creation import CreationJournal, topology_for


LEGACY_SOURCE_FILES = ('group_network.py', 'group_mesh.py', 'group_supervisor.py',
    'group_model.py', 'sglang_group.py', 'sglang_runtime.py', 'snapshots.py',
    'sglang_group_runtime.py', 'group_packager.py')
CONNECTOR_SOURCE_FILES = (*LEGACY_SOURCE_FILES, 'group_connector.py', 'server.py', 'activity.py', 'idle.py')
SOURCE_FILES = (*CONNECTOR_SOURCE_FILES, 'group_inference.py')
MAX_SOURCE = 8 << 20
MAX_ARTIFACT = 32 << 20


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_regular(path, limit):
    # O_NONBLOCK avoids hanging on a substituted FIFO before fstat can reject it.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit or Path(path).is_symlink():
            raise ValueError('Group package input must be a bounded regular file')
        value = stream.read(limit + 1)
        if len(value) != info.st_size or len(value) > limit:
            raise ValueError('Group package input changed while reading')
        return value


class GroupPackageStore:
    def __init__(self, root, *, timeout=10):
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError('Package compilation requires a bounded deadline')
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('Use a private durable group package directory')
        self.timeout = timeout

    def path(self, kind, sha):
        if kind not in {'source', 'artifact'} or not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
            raise ValueError('Use an exact original source or artifact digest')
        return self.root / (kind + '-' + sha)

    def read(self, kind, sha):
        try:
            value = read_regular(self.path(kind, sha), MAX_SOURCE if kind == 'source' else MAX_ARTIFACT)
        except FileNotFoundError:
            raise ValueError('Original group package input is missing; restore the pinned bytes before resuming') from None
        if digest(value) != sha:
            raise ValueError('Original group package input is corrupt; refusing replacement')
        return value

    def put(self, kind, value):
        limit = MAX_SOURCE if kind == 'source' else MAX_ARTIFACT
        if not isinstance(value, bytes) or not 0 < len(value) <= limit:
            raise ValueError('Group package exceeds its size bound')
        sha = digest(value)
        target = self.path(kind, sha)
        if target.exists() or target.is_symlink():
            self.read(kind, sha)
            return sha
        fd, temporary = tempfile.mkstemp(prefix='.writing-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o400)
            try:
                # Exclusive publication: concurrent builders can never overwrite
                # bytes associated with an already acknowledged content digest.
                os.link(temporary, target)
            except FileExistsError:
                self.read(kind, sha)
            if os.name != 'nt':
                directory = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            os.unlink(temporary)
        return sha

    def capture(self, record):
        """Snapshot trusted installed source before CreationJournal.create()."""
        from .managed import module
        from pantheon.apps.registry import BUILTIN_ROOT
        app = BUILTIN_ROOT / 'model-service'
        model = module('group_model').descriptor(record)
        catalog = json.loads(read_regular(app / 'engines.json', 1 << 20))
        matches = [r for r in catalog['recipes'] if r['id'] == 'sglang-0.5.20-linux-amd64']
        if len(matches) != 1:
            raise ValueError('Missing unique pinned group engine recipe')
        files = {name: read_regular((Path(__file__).parent if name in {'group_network.py', 'group_mesh.py', 'group_inference.py'}
            else app) / name, 2 << 20).decode('utf-8') for name in SOURCE_FILES}
        return self.put('source', encoded(dict(protocol=1, files=files, recipe=matches[0], model=model)))

    async def validate_plan(self, plan, source_sha256):
        """Reject impossible memory/topology choices before saving immutable intent.

        Use the same frozen compiler as later builds, in an isolated process.
        No authority, installation, weight download or engine is created.
        """
        source = json.loads(self.read('source', source_sha256))
        with tempfile.TemporaryDirectory(prefix='.validating-', dir=self.root) as temp:
            directory = Path(temp)
            for name, value in source['files'].items():
                (directory / name).write_text(value, encoding='utf-8')
            (directory / 'validation.json').write_bytes(encoded({'plan': plan, 'model': source['model']}))
            bootstrap = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
                "from sglang_group import _validate;"
                "v=json.load(open(sys.argv[1]+'/validation.json'));_validate(v['plan'],v['model'])")
            with tempfile.TemporaryFile() as errors:
                process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', bootstrap,
                    str(directory), cwd=directory,
                    env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=errors)
                try:
                    await asyncio.wait_for(process.wait(), self.timeout)
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise
                if process.returncode:
                    errors.seek(0, os.SEEK_END)
                    errors.seek(max(0, errors.tell() - 1024))
                    lines = errors.read().decode('utf-8', errors='replace').splitlines()
                    raise ValueError('Group configuration cannot be deployed: ' + (lines[-1][-256:] if lines else 'no diagnostic'))

    async def __call__(self, row, rank):
        CreationJournal(None, row['owner']).validate(row)
        if row['phase'] != 'building' or not row['authority_requested'] or not row['ca_sha256'] or row['authority_closed']:
            raise ValueError('Build only after the original authority is pinned and before cancellation')
        peers = topology_for(row['plan'])
        peers.member(rank)
        source = json.loads(self.read('source', row['source_sha256']))
        if (not isinstance(source, dict) or set(source) != {'protocol', 'files', 'recipe', 'model'}
                or type(source['protocol']) is not int or source['protocol'] != 1
                or not isinstance(source['files'], dict)
                or set(source['files']) not in (set(SOURCE_FILES), set(CONNECTOR_SOURCE_FILES), set(LEGACY_SOURCE_FILES))
                or any(not isinstance(value, str) or not value for value in source['files'].values())):
            raise ValueError('Original group source has an unsupported format')
        request = {key: row[key] for key in ('plan', 'topology', 'ca_sha256', 'source_sha256')}
        request['rank'] = rank
        with tempfile.TemporaryDirectory(prefix='.building-', dir=self.root) as temp:
            directory = Path(temp)
            for name, value in source['files'].items():
                (directory / name).write_text(value, encoding='utf-8')
            (directory / 'source-metadata.json').write_bytes(encoded({key: source[key] for key in ('recipe', 'model')}))
            (directory / 'build-request.json').write_bytes(encoded(request))
            output = directory / 'rank.tar'
            # Isolated import path, clean environment, original compiler bytes.
            # No engine dependency installation, subprocess model code or network.
            bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/group_packager.py',sys.argv[2]];runpy.run_path(sys.argv[0],run_name='__main__')"
            with tempfile.TemporaryFile() as errors:
                process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', bootstrap,
                    str(directory), str(output), cwd=directory,
                    env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=errors)
                try:
                    await asyncio.wait_for(process.wait(), self.timeout)
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise
                if process.returncode:
                    errors.seek(0, os.SEEK_END)
                    errors.seek(max(0, errors.tell() - 1024))
                    lines = errors.read().decode('utf-8', errors='replace').splitlines()
                    raise ValueError('Rank package compilation failed: ' + (lines[-1][-256:] if lines else 'no diagnostic'))
            return self.put('artifact', read_regular(output, MAX_ARTIFACT))

    def artifact(self, sha):
        """Read verified code bytes for later authenticated Fleet staging."""
        return self.read('artifact', sha)

    async def stage(self, lifecycle, row, rank):
        """Transfer the persisted rank artifact; never rebuild on missing bytes."""
        CreationJournal(None, row['owner']).validate(row)
        if row['phase'] not in {'built', 'handed_off'} or row['authority_closed']:
            raise ValueError('Stage only a complete uncancelled original creation')
        peer = topology_for(row['plan']).member(rank)
        artifact = next(a for a in row['artifacts'] if a['rank'] == rank)
        payload = await asyncio.to_thread(self.artifact, artifact['digest'])
        actual = await lifecycle.stage_exact(peer['node_id'], payload, artifact['digest'])
        if actual != artifact['digest']:
            raise ValueError('Node staging changed the original rank artifact')
        return actual
