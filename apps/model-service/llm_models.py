"""Pinned text LLMs for managed SGLang, prepared independently of engine startup.

Only explicitly listed data files (weights, configs, tokenizer, chat template)
enter an offline snapshot. No repository code, pickle checkpoint, moving
revision or install hook is executed. Serving flags come from this catalog,
never from a request.
"""
import hashlib
from importlib.util import spec_from_file_location, module_from_spec
import json
import os
from pathlib import Path
import re

_spec = spec_from_file_location('fleet_pinned_models', Path(__file__).with_name('pinned_models.py'))
_cache = module_from_spec(_spec)
_spec.loader.exec_module(_cache)

PARSERS = {'tool_call_parser': {'qwen3_coder', 'qwen25', 'llama3', 'mistral', 'deepseekv3', ''},
           'reasoning_parser': {'qwen3', 'deepseek-r1', ''}}
# Custom models pinned from Hugging Face by the Agent (resolve) carry their full
# manifest; ids are distinct from catalog ids.
CUSTOM_ID = r'hf-[a-z0-9][a-z0-9.-]{0,90}'
GPUS = {'A100', 'H100', 'L40S'}


def catalog():
    return json.loads(Path(__file__).with_name('llm-models.json').read_text())['models']


def _manifests():
    cache = os.environ.get('PANTHEON_APP_CACHE')
    return Path(cache) / 'llm-models' / 'manifests' if cache else None


def register(entry):
    """Keep a validated custom manifest on this node so later lookups by id work."""
    selected = model(entry)
    directory = _manifests()
    if directory is None:
        raise ValueError('Custom model manifests are stored in the App cache')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (selected['id'] + '.json')
    body = json.dumps(selected, sort_keys=True)
    if not path.exists() or path.read_text() != body:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(body)
        os.replace(temporary, path)
    return selected


def model(model_id):
    """A validated model entry: catalog id, registered custom id, or an explicit custom entry."""
    if isinstance(model_id, dict):
        selected = json.loads(json.dumps(model_id))
        if not re.fullmatch(CUSTOM_ID, str(selected.get('id', ''))):
            raise ValueError('Custom language models need an hf- id')
    else:
        selected = next((entry for entry in catalog() if entry['id'] == model_id), None)
        directory = _manifests()
        if not selected and directory is not None and re.fullmatch(CUSTOM_ID, str(model_id)):
            path = directory / (model_id + '.json')
            selected = json.loads(path.read_text()) if path.is_file() else None
            if selected and selected.get('id') != model_id:
                raise ValueError('Stored language model manifest changed identity')
    if not selected:
        raise ValueError('Choose a pinned language model')
    if (not re.fullmatch('[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', str(selected.get('model', '')))
            or not re.fullmatch('[a-f0-9]{40}', str(selected.get('revision', '')))
            or selected.get('operation') != 'text' or not isinstance(selected.get('license'), str)
            or not 0 < len(selected['license']) <= 64 or not isinstance(selected.get('display_name'), str)
            or not 0 < len(selected['display_name']) <= 120):
        raise ValueError('Invalid pinned language model identity')
    if (not isinstance(selected.get('supported_gpus', []), list)
            or not set(selected.get('supported_gpus', [])) <= GPUS
            or any(type(selected.get(k)) is not int or selected[k] < (256 << 20)
                   for k in ('minimum_memory_bytes', 'minimum_gpu_memory_bytes'))
            or not isinstance(selected.get('capabilities'), dict)
            or set(selected['capabilities']) != {'tools', 'reasoning', 'vision'}
            or any(type(v) is not bool for v in selected['capabilities'].values())):
        raise ValueError('Invalid pinned language model requirements')
    for key, allowed in PARSERS.items():
        if selected.get(key, '') not in allowed:
            raise ValueError('Unsupported serving parser for this model')
    if (type(selected['context_length']) is not int or type(selected['maximum_context_length']) is not int
            or not 512 <= selected['context_length'] <= selected['maximum_context_length'] <= 1048576):
        raise ValueError('Invalid pinned model context length')
    names = set()
    for file in selected['files']:
        name = file['name']
        _cache.validate_filename(name)
        if (name.casefold() in names or '/' in name
                or not (name == 'LICENSE' or name.endswith(('.safetensors', '.json', '.txt', '.md', '.jinja')))):
            raise ValueError('Invalid pinned language model filename')
        names.add(name.casefold())
        expected = f"https://huggingface.co/{selected['model']}/resolve/{selected['revision']}/{name}"
        if file['url'] != expected or file['revision'] != selected['revision']:
            raise ValueError('Language model files must use the exact pinned revision')
    required = {'config.json', 'tokenizer_config.json'}
    if (not required <= names or len(names) > 256
            or not any(name.endswith('.safetensors') for name in names)):
        raise ValueError('Language model needs a bounded manifest, safetensors weights, config and tokenizer')
    return selected


def source(selected):
    identity = json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()
    return dict(name=selected['id'], revision=selected['revision'], format='hf-llm-snapshot',
                url=f"https://huggingface.co/{selected['model']}/tree/{selected['revision']}",
                sha256=hashlib.sha256(identity).hexdigest(), size=sum(f['size'] for f in selected['files']))


def prepared(root, model_id):
    selected = model(model_id)  # an id or an explicit custom entry
    return _cache.prepared(root, selected, source(selected), 'llm-models')


def verify_directory(directory, selected):
    return _cache.verify_directory(directory, selected, source(selected))


class LanguageModelCache(_cache.PinnedModelCache):
    def __init__(self, root, artifacts, *, blob_cache=None):
        super().__init__(root, artifacts, model=model, source=source,
                         namespace='llm-models', blob_cache=blob_cache)


def served_name(selected):
    return 'fleet-llm-' + source(selected)['sha256']
