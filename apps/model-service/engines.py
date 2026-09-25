"""Pinned, node-local engine preparation. Never starts an engine or an installer.

The immutable App supplies recipes; an RPC caller can select a recipe, not a
URL, command or destination. Downloads run outside Fleet's install/start hooks.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile


def native_platform():
    system = platform.system().lower()
    machine = platform.machine()
    if system == 'windows':
        # Fleet starts Apps with a minimal environment; on Windows
        # platform.machine() reads PROCESSOR_ARCHITECTURE and is then empty.
        # The interpreter's own build platform does not depend on it.
        import sysconfig
        machine = {'win-amd64': 'amd64', 'win-arm64': 'arm64'}.get(sysconfig.get_platform(), machine)
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64', 'AMD64': 'amd64', 'ARM64': 'arm64'}.get(machine, machine)
    return f'{system}-{arch}'


def catalog():
    return json.loads(Path(__file__).with_name('engines.json').read_text())['recipes']


def recipe(recipe_id, *, target=None):
    selected = next((r for r in catalog() if r['id'] == recipe_id), None)
    if not selected or (target or native_platform()) not in selected['platforms']:
        raise ValueError('This engine recipe is not available for the selected node platform')
    return selected


def preinstalled(selected):
    """A node image (e.g. the Modal GPU node) provides this pinned engine.

    Only the recipe's exact interpreter and distribution version qualify; the
    connector never installs or upgrades it.
    """
    python = Path(selected['python'])
    if not python.is_absolute() or not python.is_file():
        return None
    packages = python.parent.parent / 'lib'
    found = list(packages.glob(f"python3.*/site-packages/{selected['engine']}-{selected['version']}.dist-info"))
    return python if len(found) == 1 else None


def requirement(selected):
    if selected.get('runtime') == 'container':
        return ''
    if selected.get('runtime') == 'preinstalled':
        return '' if preinstalled(selected) else 'This node does not provide the pinned engine runtime (use a Modal GPU node)'
    if selected['source']['format'] == 'tar.zst' and not shutil.which('zstd'):
        try:
            from compression import zstd  # Python 3.14+, optional on older nodes.
        except ImportError:
            return 'Install zstd on this node to prepare the pinned Linux engine archive'
    return ''


def relative_path(name):
    p = PurePosixPath(name.rstrip('/'))
    if (not name or p.is_absolute() or any(x in {'', '.', '..'} for x in name.rstrip('/').split('/'))
            or '\\' in name or ':' in name or str(p) in {'', '.'}
            or any(x.endswith((' ', '.')) or x.split('.')[0].upper() in
                   {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
                   for x in p.parts)):
        raise ValueError('Engine archive contains an unsafe path')
    return p


@contextmanager
def tar_stream(archive, format):
    process = None
    stream = None
    try:
        if format == 'tar.zst':
            try:
                from compression import zstd
                stream = zstd.open(archive, 'rb')
            except ImportError:
                executable = shutil.which('zstd')
                if not executable:
                    raise ValueError('This node needs zstd to unpack the pinned engine')
                process = subprocess.Popen([executable, '-q', '-d', '-c', str(archive)],
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                stream = process.stdout
            with tarfile.open(fileobj=stream, mode='r|') as tar:
                yield tar
            if process and process.wait(timeout=10) != 0:
                raise ValueError('Engine archive decompression failed')
        else:
            with tarfile.open(archive, mode='r|gz') as tar:
                yield tar
    finally:
        if stream:
            stream.close()
        if process:
            if process.poll() is None:
                process.kill()
            process.wait()


def extract(archive, destination, format, cancelled, *, max_bytes=16 << 30, max_files=50000,
            preserve_file_links=False):
    """Extract only regular files, directories and internal file links.

    Links are created last, so an archive cannot write through its own symlink.
    No modes, ownership, device nodes, xattrs or install scripts are inherited.
    """
    destination = Path(destination).resolve()
    entries, links, seen, total = [], [], set(), 0

    def check():
        if cancelled.is_set():
            raise InterruptedError('Engine preparation cancelled')

    def entry(name, kind, size=0, executable=False, source=None, link=None):
        nonlocal total
        check()
        path = relative_path(name)
        key = str(path).casefold()
        if key in seen or len(seen) >= max_files:
            raise ValueError('Engine archive contains duplicate paths or too many entries')
        seen.add(key)
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'directory':
            target.mkdir(exist_ok=True)
            return
        if kind in {'symlink', 'hardlink'}:
            if not link or '\\' in link or ':' in link or PurePosixPath(link).is_absolute():
                raise ValueError('Engine archive link escapes its installation')
            resolved = (target.parent / link if kind == 'symlink' else destination / link).resolve()
            if not resolved.is_relative_to(destination):
                raise ValueError('Engine archive link escapes its installation')
            links.append((target, resolved))
            return
        if kind != 'file' or size < 0:
            raise ValueError('Engine archive contains a special file')
        total += size
        if total > max_bytes:
            raise ValueError('Engine archive exceeds the extracted size limit')
        if shutil.disk_usage(destination).free < size + (64 << 20):
            raise ValueError('Insufficient disk space to prepare the engine')
        with open(target, 'xb') as output:
            remaining = size
            while remaining:
                check()
                block = source.read(min(1 << 20, remaining))
                if not block:
                    raise ValueError('Engine archive is truncated')
                output.write(block)
                remaining -= len(block)
            output.flush()
            os.fsync(output.fileno())
        target.chmod(0o500 if executable else 0o400)
        entries.append({'path': str(path), 'size': size, 'mtime_ns': target.stat().st_mtime_ns})

    if format == 'zip':
        with zipfile.ZipFile(archive) as zipped:
            for info in zipped.infolist():
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or info.flag_bits & 1:
                    raise ValueError('Engine ZIP links and encrypted entries are unsupported')
                if info.is_dir():
                    entry(info.filename, 'directory')
                else:
                    if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                        raise ValueError('Engine archive contains a special file')
                    with zipped.open(info) as source:
                        entry(info.filename, 'file', info.file_size, bool(mode & 0o111), source)
    elif format in {'tar.gz', 'tar.zst'}:
        with tar_stream(archive, format) as tar:
            for info in tar:
                kind = ('file' if info.isfile() else 'directory' if info.isdir() else
                        'symlink' if info.issym() else 'hardlink' if info.islnk() else 'special')
                if kind == 'file':
                    with tar.extractfile(info) as source:
                        entry(info.name, kind, info.size, bool(info.mode & 0o111), source)
                else:
                    entry(info.name, kind, link=info.linkname)
    else:
        raise ValueError('Unsupported engine archive format')
    # Resolve link chains before creating any links. Official engine bundles
    # may list a file alias before its target alias. Only a terminal regular
    # file is permitted; directory links, cycles and dangling links fail closed.
    pending, resolved_files = dict(links), {}
    for target, _ in links:
        check()
        if target.exists() or target.is_symlink():
            raise ValueError('Engine archive cannot write through a link')
        current, chain, visiting = target, [], set()
        while current in pending and current not in resolved_files:
            check()
            if current in visiting:
                raise ValueError('Engine archive contains a link cycle')
            visiting.add(current)
            chain.append(current)
            current = pending[current]
        terminal = resolved_files.get(current, current)
        if not terminal.is_file() or terminal.is_symlink():
            raise ValueError('Engine archive links must refer to extracted regular files')
        for alias in chain:
            resolved_files[alias] = terminal
    for target, _ in links:
        check()
        # Materialize a hardlink, not a symlink: immutable internal libraries
        # remain portable without granting Windows symlink privileges.
        record = {'path': target.relative_to(destination).as_posix()}
        if preserve_file_links:
            record['link'] = os.path.relpath(resolved_files[target], target.parent)
            os.symlink(record['link'], target)
        else:
            os.link(resolved_files[target], target)
        entries.append({**record, 'size': target.stat().st_size, 'mtime_ns': target.stat().st_mtime_ns})
    check()
    return entries


def installed(root, selected, *, verify_files=True):
    directory = Path(root) / 'engines' / selected['id'] / selected['source']['sha256']
    receipt = directory / 'installation.json'
    if not receipt.exists():
        return None
    record = json.loads(receipt.read_text())
    if record.get('recipe') != selected:
        raise ValueError('Installed engine identity changed; repair the owned installation')
    for entry in record['files'] if verify_files else []:
        path = directory.joinpath(*relative_path(entry['path']).parts)
        if entry.get('link'):
            if (not path.is_symlink() or os.readlink(path) != entry['link']
                    or not path.resolve().is_relative_to(directory.resolve())):
                raise ValueError('Installed engine files changed; repair the owned installation')
        elif path.is_symlink():
            raise ValueError('Installed engine files changed; repair the owned installation')
        if not path.is_file():
            raise ValueError('Installed engine files changed; repair the owned installation')
        info = path.stat()
        if info.st_size != entry['size'] or info.st_mtime_ns != entry['mtime_ns']:
            raise ValueError('Installed engine files changed; repair the owned installation')
    binary = directory.joinpath(*relative_path(selected['executable']).parts)
    if not binary.is_file():
        raise ValueError('Installed engine executable is missing')
    return binary


def scoped_runtime(root, selected, scope):
    if not isinstance(scope, str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}', scope):
        raise ValueError('An exact managed engine scope is required')
    return Path(root) / 'engine-runs' / scope / selected['id'] / selected['source']['sha256']


def prepared(root, selected, scope=None, *, verify_files=True):
    if selected.get('runtime') == 'container':
        return None  # Fleet owns image inspection/pull; connector never controls Docker.
    if selected.get('runtime') == 'preinstalled':
        return preinstalled(selected)
    binary = installed(root, selected, verify_files=verify_files)
    if not binary or selected['engine'] != 'lmstudio':
        return binary
    directory = scoped_runtime(root, selected, scope)
    receipt = directory / 'prepared.json'
    if not receipt.exists():
        return None
    record = json.loads(receipt.read_text())
    binary = directory / 'runtime' / selected['executable']
    if (record.get('recipe') != selected or binary.is_symlink() or not binary.is_file()
            or record.get('size') != binary.stat().st_size or record.get('mtime_ns') != binary.stat().st_mtime_ns):
        raise ValueError('Owned engine runtime changed; repair this deployment')
    return binary


class EngineCache:
    """DownloadJobs adapter: ready means verified and atomically installed."""
    def __init__(self, root, blobs, lock, atomic_json, scope=None):
        self.root, self.blobs = Path(root), blobs
        self.lock, self.atomic_json = lock, atomic_json
        self.scope = scope

    def prepare_scoped(self, selected, binary, cancelled, progress):
        if selected['engine'] != 'lmstudio':
            return binary
        destination = scoped_runtime(self.root, selected, self.scope)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.lock(destination.parent / 'prepare.lock'):
            if ready := prepared(self.root, selected, self.scope):
                return ready
            progress('installing', selected['source']['size'])
            # llmster moves its bundled runtimes into its HOME on first start.
            # Give each owned deployment a copy; it must never mutate the shared
            # immutable recipe cache or another user's LM Studio installation.
            def copy(source, target):
                if cancelled.is_set():
                    raise InterruptedError('Engine preparation cancelled')
                if shutil.disk_usage(destination.parent).free < os.stat(source).st_size + (64 << 20):
                    raise ValueError('Insufficient disk space for the owned engine runtime')
                return shutil.copy2(source, target)
            with tempfile.TemporaryDirectory(prefix='preparing-', dir=destination.parent) as temp:
                runtime = Path(temp) / 'runtime'
                source = binary.parent
                shutil.copytree(source, runtime, copy_function=copy, symlinks=True)
                executable = runtime / selected['executable']
                self.atomic_json(Path(temp) / 'prepared.json', {'recipe': selected,
                    'size': executable.stat().st_size, 'mtime_ns': executable.stat().st_mtime_ns})
                if destination.exists():
                    raise ValueError('Incomplete owned runtime needs repair; existing files preserved')
                os.rename(temp, destination)
        return prepared(self.root, selected, self.scope)

    def fetch(self, source, cancelled, progress):
        selected = next((r for r in catalog() if r.get('source') == source and native_platform() in r['platforms']), None)
        if not selected:
            raise ValueError('Engine downloads must match an immutable node-compatible recipe')
        if error := requirement(selected):
            raise ValueError(error)
        directory = self.root / 'engines' / selected['id']
        directory.mkdir(parents=True, exist_ok=True)
        with self.lock(directory / 'prepare.lock'):
            if binary := installed(self.root, selected):
                binary = self.prepare_scoped(selected, binary, cancelled, progress)
                progress('ready', source['size'])
                return binary
            archive = self.blobs.fetch(source, cancelled,
                lambda state, done: progress('installing' if state == 'ready' else state, done))
            with tempfile.TemporaryDirectory(prefix='preparing-', dir=directory) as temporary:
                files = extract(archive, temporary, source['format'], cancelled,
                                preserve_file_links=selected.get('file_symlinks', False))
                if not Path(temporary, selected['executable']).is_file():
                    raise ValueError('Pinned engine archive has no expected executable')
                self.atomic_json(Path(temporary) / 'installation.json', {'recipe': selected, 'files': files})
                destination = directory / source['sha256']
                if destination.exists():
                    raise ValueError('Incomplete engine installation needs repair; existing files preserved')
                # TemporaryDirectory removes only its temporary pathname. The
                # committed version is never replaced or deleted on failure.
                os.rename(temporary, destination)
            binary = self.prepare_scoped(selected, installed(self.root, selected), cancelled, progress)
            progress('ready', source['size'])
            return binary
