"""Pinned diffusion weights, downloaded independently of engine startup.

Only explicitly listed data files enter an offline snapshot. No repository code,
pickle checkpoints, moving revision, or model-install hook is executed.
"""
import hashlib
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import re

_spec = spec_from_file_location('fleet_pinned_models', Path(__file__).with_name('pinned_models.py'))
_cache = module_from_spec(_spec)
_spec.loader.exec_module(_cache)


def catalog():
    return json.loads(Path(__file__).with_name('diffusion-models.json').read_text())['models']


def model(model_id):
    selected = next((entry for entry in catalog() if entry['id'] == model_id), None)
    if not selected:
        raise ValueError('Choose a pinned diffusion model')
    if (not re.fullmatch('[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', selected['model'])
            or not re.fullmatch('[a-f0-9]{40}', selected['revision'])
            or selected.get('operation') != 'image'):
        raise ValueError('Invalid diffusion model identity')
    names, components = set(), {}
    for file in selected['files']:
        name = file['name']
        _cache.validate_filename(name)
        if (name.casefold() in names or not name.endswith(('.safetensors', '.json', '.txt', '.md'))):
            raise ValueError('Invalid pinned diffusion model filename')
        # Reject case collisions and file/directory conflicts on all platforms.
        parts = name.split('/')
        for i in range(1, len(parts)):
            parent = '/'.join(parts[:i])
            if (parent.casefold() in names
                    or components.get(parent.casefold(), parent) != parent):
                raise ValueError('Pinned model file conflicts with a directory')
            components[parent.casefold()] = parent
        if any(n.startswith(name.casefold() + '/') for n in names):
            raise ValueError('Pinned model directory conflicts with a file')
        names.add(name.casefold())
        expected = f"https://huggingface.co/{selected['model']}/resolve/{selected['revision']}/{name}"
        if file['url'] != expected or file['revision'] != selected['revision']:
            raise ValueError('Diffusion model files must use the exact pinned revision')
    if (not {'readme.md', 'license.md', 'model_index.json'} <= names or len(names) > 128
            or not any(name.endswith('.safetensors') for name in names)):
        raise ValueError('Diffusion model needs a bounded manifest, weights, model card and license')
    return selected


def source(selected):
    identity = json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()
    return dict(name=selected['id'], revision=selected['revision'], format='hf-diffusion-snapshot',
                url=f"https://huggingface.co/{selected['model']}/tree/{selected['revision']}",
                sha256=hashlib.sha256(identity).hexdigest(), size=sum(f['size'] for f in selected['files']))


def prepared(root, model_id):
    selected = model(model_id)
    return _cache.prepared(root, selected, source(selected), 'diffusion-models')


def verify_directory(directory, selected):
    return _cache.verify_directory(directory, selected, source(selected))


class DiffusionModelCache(_cache.PinnedModelCache):
    def __init__(self, root, artifacts, *, blob_cache=None):
        super().__init__(root, artifacts, model=model, source=source,
                         namespace='diffusion-models', blob_cache=blob_cache)


def served_name(selected):
    return 'fleet-diffusion-' + source(selected)['sha256']
