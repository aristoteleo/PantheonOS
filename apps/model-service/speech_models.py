"""Pinned speech models, prepared separately from engine startup.

The job source describes an aggregate snapshot, not a downloadable archive.
Its digest covers the pinned file manifest; its URL is provenance only. Each
file uses ArtifactCache's resumable, SHA256-verified transfer. The resulting
HF cache is private to one snapshot and can be mounted read-only by the engine.
No huggingface_hub dependency, model code or install hook runs here.
"""
import hashlib
import json
from pathlib import Path
import re


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


# Load from this immutable App package, including embedded/import contexts.
from importlib.util import spec_from_file_location, module_from_spec
_spec = spec_from_file_location('fleet_pinned_models', Path(__file__).with_name('pinned_models.py'))
_cache = module_from_spec(_spec)
_spec.loader.exec_module(_cache)


def prepared(root, model_id):
    selected = model(model_id)
    return _cache.prepared(root, selected, source(selected), 'speech-models')


def verify_directory(directory, selected):
    return _cache.verify_directory(directory, selected, source(selected))


class SpeechModelCache(_cache.PinnedModelCache):
    def __init__(self, root, artifacts, *, blob_cache=None):
        super().__init__(root, artifacts, model=model, source=source,
                         namespace='speech-models', blob_cache=blob_cache)
