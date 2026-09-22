"""Immutable safetensors snapshots prepared from an already verified artifact.

The format is a tar.gz with config/tokenizer/safetensors at its root. There are
no executable model files, pickle weights, links, network calls or installers.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile


def snapshot(root, digest, *, verify=True):
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Choose an exact verified model snapshot')
    directory = Path(root) / 'snapshots' / digest
    receipt = directory / 'snapshot.json'
    if not receipt.is_file():
        return None
    record = json.loads(receipt.read_text())
    if record.get('sha256') != digest:
        raise ValueError('Model snapshot identity changed')
    for item in record['files'] if verify else []:
        path = directory / item['path']
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size'] or path.stat().st_mtime_ns != item['mtime_ns']:
            raise ValueError('Prepared model files changed; repair the owned snapshot')
    return record


def memory_estimate(record, context, parallel):
    """Conservative decoder KV estimate; refuse architectures we cannot account for."""
    config = record['config']
    # Hybrid/MLA/quantized/vision models need an engine-specific accounting
    # rule. They remain usable through an attached service until supported.
    if config.get('model_type') not in {'qwen2', 'llama', 'mistral', 'gemma', 'gemma2'} or config.get('quantization_config'):
        raise ValueError('This architecture needs a supported managed memory estimator; use an attached engine')
    def integer(key):
        n = config.get(key)
        if type(n) is not int or not 0 < n < (1 << 24):
            raise ValueError('Missing model dimensions for memory estimation')
        return n
    layers, heads, hidden = integer('num_hidden_layers'), integer('num_attention_heads'), integer('hidden_size')
    kv_heads = config.get('num_key_value_heads', heads)
    head_dim = config.get('head_dim', hidden // heads)
    if type(kv_heads) is not int or not 0 < kv_heads <= heads or type(head_dim) is not int or not 0 < head_dim <= hidden:
        raise ValueError('Invalid model KV dimensions')
    # Pinned launch explicitly uses float16 for weights and KV. An FP32
    # source stays conservatively counted at its on-disk size.
    kv = 2 * layers * kv_heads * head_dim * context * parallel * 2
    weights = record['weights_bytes']
    workspace = max(2 << 30, weights // 5)
    return dict(weights_bytes=weights, kv_bytes=kv, workspace_bytes=workspace,
                estimated_bytes=weights + kv + workspace,
                context_length=context, parallel=parallel, estimated=True)


class SnapshotCache:
    def __init__(self, root, artifacts):
        self.root, self.artifacts = Path(root), artifacts

    def fetch(self, source, cancelled, progress):
        source = self.artifacts.validate_source(source)
        if source['format'] != 'safetensors.tar.gz':
            raise ValueError('Choose a verified safetensors.tar.gz model bundle')
        digest = source['sha256']
        archive = self.root / 'blobs' / digest
        parent = self.root / 'snapshots'
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.artifacts.file_lock(parent / (digest + '.lock')):
            if snapshot(self.root, digest):
                progress('ready', source['size'])
                return parent / digest
            if archive.is_symlink() or not archive.is_file():
                raise ValueError('Download and verify the model bundle before preparing it')
            progress('verifying', source['size'])
            self.artifacts.ArtifactCache.verify(archive, digest, source['size'], cancelled)
            progress('installing', source['size'])
            with tempfile.TemporaryDirectory(prefix='preparing-', dir=parent) as temp:
                files, names, expanded = [], set(), 0
                weights = 0
                with tarfile.open(archive, 'r|gz') as tar:
                    for entry in tar:
                        if cancelled.is_set():
                            raise self.artifacts.Cancelled()
                        name = entry.name
                        if (not entry.isfile() or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}', name)
                                or name == 'snapshot.json' or name.casefold() in names or len(names) >= 4096
                                or not name.endswith(('.json', '.safetensors', '.txt', '.model', '.tiktoken'))):
                            raise ValueError('Model bundle must contain only root-level safetensors and tokenizer data files')
                        names.add(name.casefold())
                        expanded += entry.size
                        if entry.size <= 0 or expanded > 1 << 40:
                            raise ValueError('Model bundle exceeds the supported size')
                        if not name.endswith('.safetensors') and entry.size > 128 << 20:
                            raise ValueError('Model metadata is too large')
                        if shutil.disk_usage(temp).free < entry.size + (256 << 20):
                            raise ValueError('Insufficient disk space to prepare the model')
                        path = Path(temp) / name
                        checksum = hashlib.sha256()
                        with tar.extractfile(entry) as incoming, path.open('wb') as output:
                            while block := incoming.read(1 << 20):
                                if cancelled.is_set():
                                    raise self.artifacts.Cancelled()
                                checksum.update(block)
                                output.write(block)
                            output.flush(); os.fsync(output.fileno())
                        if name.endswith('.safetensors'):
                            # Validate the bounded header before declaring this a
                            # safe tensor file. Payload interpretation stays with
                            # the pinned engine; no pickle fallback is allowed.
                            with path.open('rb') as stream:
                                length = int.from_bytes(stream.read(8), 'little')
                                if not 2 <= length <= min(16 << 20, entry.size - 8):
                                    raise ValueError('Invalid safetensors header')
                                header = json.loads(stream.read(length))
                                if not isinstance(header, dict) or not any(k != '__metadata__' for k in header):
                                    raise ValueError('Empty safetensors file')
                            weights += entry.size
                        path.chmod(0o400)
                        files.append(dict(path=name, size=entry.size, sha256=checksum.hexdigest(), mtime_ns=path.stat().st_mtime_ns))
                if not weights or 'config.json' not in names or not names.intersection({'tokenizer.json', 'tokenizer.model', 'tokenizer.tiktoken'}):
                    raise ValueError('Model bundle needs config, tokenizer and safetensors weights')
                config_path = Path(temp) / 'config.json'
                if config_path.stat().st_size > 1 << 20:
                    raise ValueError('Model configuration exceeds metadata limit')
                config = json.loads(config_path.read_text())
                if not isinstance(config, dict) or config.get('auto_map'):
                    raise ValueError('Managed models cannot request executable remote model code')
                record = dict(sha256=digest, revision=source['revision'], name=source['name'],
                              files=files, config=config, weights_bytes=weights)
                self.artifacts.atomic_json(Path(temp) / 'snapshot.json', record)
                os.rename(temp, parent / digest)
            progress('ready', source['size'])
            return parent / digest
