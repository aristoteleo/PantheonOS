"""CPU-only preparation of a public, commit-pinned model for the GPU test."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import threading

sys.path.insert(0, '/opt/connector')
import artifacts
from snapshots import SnapshotCache, snapshot

root = Path('/opt/model-cache')
(root / 'blobs').mkdir(parents=True, exist_ok=True)
archive = root / 'model.tar.gz'
with tarfile.open(archive, 'w:gz', compresslevel=1) as tar:
    for path in sorted(Path('/opt/model').iterdir()):
        if path.is_file() and path.suffix in {'.json', '.safetensors', '.model', '.tiktoken', '.txt'}:
            tar.add(path, arcname=path.name, recursive=False)
hasher = hashlib.sha256()
with archive.open('rb') as stream:
    while block := stream.read(1 << 20): hasher.update(block)
digest = hasher.hexdigest()
source = dict(url='https://example.invalid/offline-fixture.tar.gz', sha256=digest, size=archive.stat().st_size,
              name='Qwen2.5-0.5B-Instruct', revision='7ae557604adf67be50417f59c2c2f167def9a775', format='safetensors.tar.gz')
archive.rename(root / 'blobs' / digest)
path = SnapshotCache(root, artifacts).fetch(source, threading.Event(), lambda *args: None)
assert SnapshotCache(root, artifacts).fetch(source, threading.Event(), lambda *args: None) == path
Path('/fleet').mkdir(exist_ok=True)
Path('/fleet/weights').symlink_to(path, target_is_directory=True)
Path('/opt/model-snapshot.json').write_text(json.dumps(source))
print(json.dumps({'snapshot': digest, 'weights_bytes': snapshot(root, digest)['weights_bytes']}))
