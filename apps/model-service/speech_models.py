"""Pinned speech models, prepared separately from engine startup.

The job source describes an aggregate snapshot, not a downloadable archive.
Its digest covers the pinned file manifest; its URL is provenance only. Each
file uses ArtifactCache's resumable, SHA256-verified transfer. The resulting
HF cache is private to one snapshot and can be mounted read-only by the engine.
No huggingface_hub dependency, model code or install hook runs here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def catalog():
    return json.loads(Path(__file__).with_name('speech-models.json').read_text())['models']


def model(model_id):
    selected = next((entry for entry in catalog() if entry['id'] == model_id), None)
    if not selected:
        raise ValueError('Choose a pinned speech model')
    if (not re.fullmatch('[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', selected['model'])
            or not re.fullmatch('[a-f0-9]{40}', selected['revision'])):
        raise ValueError('Invalid speech model identity')
    names = set()
    for file in selected['files']:
        name = file['name']
        if (not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}', name)
                or name.casefold() in names or name.split('.')[0].upper() in
                {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
                or not name.endswith(('.onnx', '.bin', '.json', '.txt', '.md'))):
            raise ValueError('Invalid pinned speech model filename')
        names.add(name.casefold())
        expected = f"https://huggingface.co/{selected['model']}/resolve/{selected['revision']}/{name}"
        if file['url'] != expected or file['revision'] != selected['revision']:
            raise ValueError('Speech model files must use the exact pinned revision')
    if not names or len(names) > 64 or 'readme.md' not in names:
        raise ValueError('Speech model needs a bounded file manifest and model card')
    return selected


def source(selected):
    identity = json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()
    return dict(name=selected['id'], revision=selected['revision'], format='hf-speech-snapshot',
                url=f"https://huggingface.co/{selected['model']}/tree/{selected['revision']}",
                sha256=hashlib.sha256(identity).hexdigest(), size=sum(f['size'] for f in selected['files']))


def regular(path, base):
    """Reject directory links as well as file links within our owned tree."""
    current = path
    while current != base:
        if current.is_symlink():
            raise ValueError('Prepared speech model cannot contain links')
        current = current.parent
    if not path.is_file():
        raise ValueError('Prepared speech model file is missing')
    return path.stat()


def prepared(root, model_id):
    selected = model(model_id)
    descriptor = source(selected)
    parent = Path(root) / 'speech-models'
    directory = parent / descriptor['sha256']
    if parent.is_symlink() or directory.is_symlink():
        raise ValueError('Prepared speech model cannot contain links')
    if not directory.exists():
        return None
    return verify_directory(directory, selected)


def verify_directory(directory, selected):
    directory = Path(directory)
    receipt = directory / 'snapshot.json'
    if directory.is_symlink() or regular(receipt, directory).st_size > 64 << 10:
        raise ValueError('Invalid speech model receipt')
    record = json.loads(receipt.read_text())
    if record.get('source') != source(selected):
        raise ValueError('Prepared speech model identity changed')
    expected = {file['name']: file for file in selected['files']}
    records = record.get('files')
    if (not isinstance(records, list) or len(records) != len(expected)
            or {file.get('name') for file in records} != set(expected)):
        raise ValueError('Prepared speech model manifest changed')
    repo = directory / 'hub' / ('models--' + selected['model'].replace('/', '--'))
    ref = repo / 'refs' / 'main'
    if regular(ref, directory).st_size != 40 or ref.read_text() != selected['revision']:
        raise ValueError('Prepared speech model revision changed')
    for file in records:
        pin = expected[file['name']]
        path = repo / 'snapshots' / selected['revision'] / file['name']
        stat = regular(path, directory)
        if (file.get('sha256') != pin['sha256'] or stat.st_size != pin['size']
                or file.get('size') != pin['size'] or stat.st_mtime_ns != file.get('mtime_ns')
                or stat.st_ctime_ns != file.get('ctime_ns')):
            raise ValueError('Prepared speech model files changed; repair the owned snapshot')
    return record


class SpeechModelCache:
    def __init__(self, root, artifacts, *, blob_cache=None):
        self.root, self.artifacts = Path(root), artifacts
        self.blobs = blob_cache or artifacts.ArtifactCache(self.root / 'blobs')

    def fetch(self, descriptor, cancelled, progress):
        descriptor = self.artifacts.validate_source(descriptor)
        selected = model(descriptor['name'])
        if descriptor != source(selected):
            raise ValueError('Speech model job must match its pinned manifest')
        for file in selected['files']:
            self.artifacts.validate_source(file)
        parent = self.root / 'speech-models'
        if parent.is_symlink():
            raise ValueError('Speech model cache cannot be a link')
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = parent / descriptor['sha256']
        with self.artifacts.file_lock(parent / (descriptor['sha256'] + '.lock')):
            if cancelled.is_set():
                raise self.artifacts.Cancelled()
            if prepared(self.root, selected['id']):
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
                    raise ValueError('Speech model blob cannot be a link')
                paths.append((file, blob))
                done += file['size']
            if shutil.disk_usage(parent).free < descriptor['size'] + (64 << 20):
                raise ValueError('Insufficient disk space to prepare the speech model')
            progress('installing', done)
            with tempfile.TemporaryDirectory(prefix='preparing-', dir=parent) as temporary:
                directory = Path(temporary)
                repo = directory / 'hub' / ('models--' + selected['model'].replace('/', '--'))
                snapshot = repo / 'snapshots' / selected['revision']
                snapshot.mkdir(parents=True)
                files = []
                for pin, blob in paths:
                    path = snapshot / pin['name']
                    checksum, size = hashlib.sha256(), 0
                    with blob.open('rb') as incoming, path.open('xb') as outgoing:
                        while block := incoming.read(1 << 20):
                            if cancelled.is_set():
                                raise self.artifacts.Cancelled()
                            size += len(block)
                            if size > pin['size']:
                                raise ValueError('Speech model blob exceeds its pinned size')
                            checksum.update(block)
                            outgoing.write(block)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    if size != pin['size'] or checksum.hexdigest() != pin['sha256']:
                        raise ValueError('Speech model blob checksum mismatch')
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
