"""Atomic, offline HF snapshots built from a validated fixed file manifest.

Catalog adapters choose the identity and allowed file types. Transfers use the
shared resumable blob cache; a partial snapshot never becomes engine-visible.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def validate_filename(name):
    if not isinstance(name, str) or len(name) > 240 or len(name.split('/')) > 8:
        raise ValueError('Invalid pinned model filename')
    for part in name.split('/'):
        if (not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}', part)
                or part.endswith('.') or part.split('.')[0].upper() in
                {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
            raise ValueError('Invalid pinned model filename')


def regular(path, base):
    """Reject directory links as well as file links within our owned tree."""
    current = path
    while current != base:
        if current.is_symlink():
            raise ValueError('Prepared model snapshot cannot contain links')
        current = current.parent
    if not path.is_file():
        raise ValueError('Prepared model snapshot file is missing')
    return path.stat()


def prepared(root, selected, descriptor, namespace):
    parent = Path(root) / namespace
    directory = parent / descriptor['sha256']
    if parent.is_symlink() or directory.is_symlink():
        raise ValueError('Prepared model snapshot cannot contain links')
    if not directory.exists():
        return None
    return verify_directory(directory, selected, descriptor)


def verify_directory(directory, selected, descriptor):
    directory = Path(directory)
    receipt = directory / 'snapshot.json'
    if directory.is_symlink() or regular(receipt, directory).st_size > 64 << 10:
        raise ValueError('Invalid model snapshot receipt')
    record = json.loads(receipt.read_text())
    if record.get('source') != descriptor:
        raise ValueError('Prepared model snapshot identity changed')
    expected = {file['name']: file for file in selected['files']}
    records = record.get('files')
    if (not isinstance(records, list) or len(records) != len(expected)
            or {file.get('name') for file in records} != set(expected)):
        raise ValueError('Prepared model snapshot manifest changed')
    repo = directory / 'hub' / ('models--' + selected['model'].replace('/', '--'))
    ref = repo / 'refs' / 'main'
    if regular(ref, directory).st_size != 40 or ref.read_text() != selected['revision']:
        raise ValueError('Prepared model snapshot revision changed')
    for file in records:
        pin = expected[file['name']]
        path = repo / 'snapshots' / selected['revision'] / file['name']
        stat = regular(path, directory)
        if (file.get('sha256') != pin['sha256'] or stat.st_size != pin['size']
                or file.get('size') != pin['size'] or stat.st_mtime_ns != file.get('mtime_ns')
                or stat.st_ctime_ns != file.get('ctime_ns')):
            raise ValueError('Prepared model snapshot files changed; repair the owned snapshot')
    return record


class PinnedModelCache:
    def __init__(self, root, artifacts, *, model, source, namespace, blob_cache=None):
        if not re.fullmatch('[a-z][a-z0-9-]{0,63}', namespace):
            raise ValueError('Invalid model cache namespace')
        self.model, self.source, self.namespace = model, source, namespace
        self.root, self.artifacts = Path(root), artifacts
        self.blobs = blob_cache or artifacts.ArtifactCache(self.root / 'blobs')

    def fetch(self, descriptor, cancelled, progress):
        descriptor = self.artifacts.validate_source(descriptor)
        selected = self.model(descriptor['name'])
        if descriptor != self.source(selected):
            raise ValueError('Model snapshot job must match its pinned manifest')
        for file in selected['files']:
            self.artifacts.validate_source(file)
            validate_filename(file['name'])
        parent = self.root / self.namespace
        if parent.is_symlink():
            raise ValueError('Model snapshot cache cannot be a link')
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = parent / descriptor['sha256']
        with self.artifacts.file_lock(parent / (descriptor['sha256'] + '.lock')):
            if cancelled.is_set():
                raise self.artifacts.Cancelled()
            if prepared(self.root, selected, descriptor, self.namespace):
                progress('ready', descriptor['size'])
                return target
            done, paths = 0, []
            for file in selected['files']:
                def update(state, count):
                    # Individual file completion must not mark the aggregate
                    # job ready before every file and its receipt are committed.
                    progress('downloading' if state == 'ready' else state, done + count)
                blob = self.blobs.fetch(file, cancelled, update)
                if blob.is_symlink():
                    raise ValueError('Model snapshot blob cannot be a link')
                paths.append((file, blob))
                done += file['size']
            if shutil.disk_usage(parent).free < descriptor['size'] + (64 << 20):
                raise ValueError('Insufficient disk space to prepare the model snapshot')
            progress('installing', done)
            with tempfile.TemporaryDirectory(prefix='preparing-', dir=parent) as temporary:
                directory = Path(temporary)
                repo = directory / 'hub' / ('models--' + selected['model'].replace('/', '--'))
                snapshot = repo / 'snapshots' / selected['revision']
                snapshot.mkdir(parents=True)
                files = []
                for pin, blob in paths:
                    path = snapshot / pin['name']
                    path.parent.mkdir(parents=True, exist_ok=True)
                    checksum, size = hashlib.sha256(), 0
                    with blob.open('rb') as incoming, path.open('xb') as outgoing:
                        while block := incoming.read(1 << 20):
                            if cancelled.is_set():
                                raise self.artifacts.Cancelled()
                            size += len(block)
                            if size > pin['size']:
                                raise ValueError('Model snapshot blob exceeds its pinned size')
                            checksum.update(block)
                            outgoing.write(block)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    if size != pin['size'] or checksum.hexdigest() != pin['sha256']:
                        raise ValueError('Model snapshot blob checksum mismatch')
                    path.chmod(0o400)
                    stat = path.stat()
                    files.append(dict(name=pin['name'], sha256=pin['sha256'], size=size,
                                      mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns))
                refs = repo / 'refs'
                refs.mkdir()
                with (refs / 'main').open('x') as stream:
                    stream.write(selected['revision'])
                    stream.flush()
                    os.fsync(stream.fileno())
                (refs / 'main').chmod(0o400)
                self.artifacts.atomic_json(directory / 'snapshot.json', dict(source=descriptor, files=files))
                if cancelled.is_set():
                    raise self.artifacts.Cancelled()
                os.rename(directory, target)
            progress('ready', done)
            return target
