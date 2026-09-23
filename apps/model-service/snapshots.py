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


def prepared_parallel_snapshot(root, digest, artifacts):
    """Upgrade only the cached receipt under its preparation lock; never download."""
    directory = Path(root) / 'snapshots'
    # Validate the digest before constructing the lock path.
    record = snapshot(root, digest)
    if not record or 'tensor_parallel_weights' in record:
        return record
    with artifacts.file_lock(directory / (digest + '.lock')):
        record = snapshot(root, digest)
        if record and 'tensor_parallel_weights' not in record:
            record['tensor_parallel_weights'] = parallel_weight_estimates(directory / digest, record)
            artifacts.atomic_json(directory / digest / 'snapshot.json', record)
        return record


def memory_estimate(record, context, parallel, tensor_parallel_size=1):
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
    tp = tensor_parallel_size
    if type(tp) is not int or tp not in {1, 2, 4, 8}:
        raise ValueError('Tensor parallel size must be 1, 2, 4 or 8')
    if heads % tp or (kv_heads >= tp and kv_heads % tp) or (kv_heads < tp and tp % kv_heads):
        raise ValueError('Attention and KV heads cannot be partitioned over these tensor parallel ranks')
    # Pinned launch explicitly uses float16 for weights and KV. An FP32
    # source stays conservatively counted at its on-disk size.
    kv = 2 * layers * max(1, kv_heads // tp) * head_dim * context * parallel * 2
    weights = record['weights_bytes']
    if tp > 1:
        if 'tensor_parallel_weights' not in record:
            raise ValueError('Prepare this cached snapshot again to account for tensor parallel weights')
        weights = record['tensor_parallel_weights'].get(str(tp))
        if type(weights) is not int or weights <= 0:
            raise ValueError('This snapshot needs a supported tensor-parallel weight estimator; use an attached engine')
    workspace = max(2 << 30, weights // 5)
    return dict(weights_bytes=weights, kv_bytes=kv, workspace_bytes=workspace,
                estimated_bytes=weights + kv + workspace,
                context_length=context, parallel=parallel, tensor_parallel_size=tp,
                per_device=True, estimated=True)


def parallel_weight_estimates(directory, record):
    """Read bounded headers once; unknown tensors and embeddings stay replicated.

    Only standard decoder projection names get sharding credit. This deliberately
    overestimates vocab padding/tied heads and architectures with unusual loaders.
    """
    estimates = {str(tp): record['weights_bytes'] for tp in (2, 4, 8)}
    seen = set()
    config = record['config']
    heads = config.get('num_attention_heads')
    kv_heads = config.get('num_key_value_heads', heads)
    if type(kv_heads) is not int or kv_heads < 1:
        return {}
    for item in record['files']:
        if not item['path'].endswith('.safetensors'):
            continue
        with (Path(directory) / item['path']).open('rb') as stream:
            length = int.from_bytes(stream.read(8), 'little')
            if not 2 <= length <= min(16 << 20, item['size'] - 8):
                raise ValueError('Invalid safetensors header')
            header = json.loads(stream.read(length))
        if not isinstance(header, dict):
            raise ValueError('Invalid safetensors header')
        ranges = []
        for name, value in header.items():
            if name == '__metadata__':
                continue
            if name in seen or len(seen) >= 100000:
                raise ValueError('Duplicate or excessive model tensor metadata')
            seen.add(name)
            if not isinstance(value, dict) or value.get('dtype') not in {'F16', 'BF16', 'F32', 'F64'}:
                return {}  # No sharding admission for an unaccounted dtype.
            shape, offsets = value.get('shape'), value.get('data_offsets')
            if (not isinstance(shape, list) or len(shape) > 8 or
                    any(type(n) is not int or n < 1 or n > 1 << 30 for n in shape) or
                    not isinstance(offsets, list) or len(offsets) != 2 or
                    any(type(n) is not int for n in offsets)):
                raise ValueError('Invalid safetensors tensor dimensions')
            size = {'F16': 2, 'BF16': 2, 'F32': 4, 'F64': 8}[value['dtype']]
            for n in shape:
                size *= n
            if not 0 <= offsets[0] <= offsets[1] <= item['size'] - 8 - length or offsets[1] - offsets[0] != size:
                raise ValueError('Invalid safetensors tensor byte range')
            ranges.append(tuple(offsets))
            # Only the pinned Qwen2 loader's projection layout is accounted for.
            # Other architectures remain conservatively replicated per rank.
            if config.get('model_type') != 'qwen2':
                continue
            match = re.fullmatch(r'model\.layers\.\d+\.(self_attn|mlp)\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)\.(weight|bias)', name)
            if not match:
                continue
            family, projection, kind = match.groups()
            if (family == 'self_attn') != (projection in {'q_proj', 'k_proj', 'v_proj', 'o_proj'}):
                continue
            row_parallel = projection in {'o_proj', 'down_proj'}
            if row_parallel and kind == 'bias':
                continue
            axis = 1 if row_parallel else 0
            if len(shape) != (2 if kind == 'weight' else 1):
                continue
            for tp in (2, 4, 8):
                partitions = min(tp, kv_heads) if projection in {'k_proj', 'v_proj'} else tp
                if shape[axis] % partitions:
                    continue  # Malformed/unsupported dimensions get no sharding credit.
                estimates[str(tp)] -= size - size // partitions
        end = 0
        for start, stop in sorted(ranges):
            if start < end:
                raise ValueError('Overlapping safetensors tensor byte ranges')
            end = stop
    return estimates


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
            cached = snapshot(self.root, digest)
            if cached:
                if 'tensor_parallel_weights' not in cached:
                    cached['tensor_parallel_weights'] = parallel_weight_estimates(parent / digest, cached)
                    self.artifacts.atomic_json(parent / digest / 'snapshot.json', cached)
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
                record['tensor_parallel_weights'] = parallel_weight_estimates(temp, record)
                self.artifacts.atomic_json(Path(temp) / 'snapshot.json', record)
                os.rename(temp, parent / digest)
            progress('ready', source['size'])
            return parent / digest
