"""Pinned text LLMs for managed SGLang, prepared independently of engine startup.

Only explicitly listed data files (weights, configs, tokenizer, chat template)
enter an offline snapshot. No repository code, pickle checkpoint, moving
revision or install hook is executed. Serving flags come from this catalog,
never from a request.
"""
import hashlib
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import re

_spec = spec_from_file_location('fleet_pinned_models', Path(__file__).with_name('pinned_models.py'))
_cache = module_from_spec(_spec)
_spec.loader.exec_module(_cache)

PARSERS = {'tool_call_parser': {'qwen3_coder', 'qwen25', 'llama3', 'mistral', 'deepseekv3'},
           'reasoning_parser': {'qwen3', 'deepseek-r1', ''}}


def catalog():
    return json.loads(Path(__file__).with_name('llm-models.json').read_text())['models']


def model(model_id):
    selected = next((entry for entry in catalog() if entry['id'] == model_id), None)
    if not selected:
        raise ValueError('Choose a pinned language model')
    if (not re.fullmatch('[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', selected['model'])
            or not re.fullmatch('[a-f0-9]{40}', selected['revision'])
            or selected.get('operation') != 'text' or selected.get('license') != 'apache-2.0'):
        raise ValueError('Invalid pinned language model identity')
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
    required = {'readme.md', 'license', 'config.json', 'tokenizer_config.json'}
    if (not required <= names or len(names) > 256
            or not any(name.endswith('.safetensors') for name in names)):
        raise ValueError('Language model needs a bounded manifest, weights, config, tokenizer and license')
    return selected


def source(selected):
    identity = json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()
    return dict(name=selected['id'], revision=selected['revision'], format='hf-llm-snapshot',
                url=f"https://huggingface.co/{selected['model']}/tree/{selected['revision']}",
                sha256=hashlib.sha256(identity).hexdigest(), size=sum(f['size'] for f in selected['files']))


def prepared(root, model_id):
    selected = model(model_id)
    return _cache.prepared(root, selected, source(selected), 'llm-models')


def verify_directory(directory, selected):
    return _cache.verify_directory(directory, selected, source(selected))


class LanguageModelCache(_cache.PinnedModelCache):
    def __init__(self, root, artifacts, *, blob_cache=None):
        super().__init__(root, artifacts, model=model, source=source,
                         namespace='llm-models', blob_cache=blob_cache)


def served_name(selected):
    return 'fleet-llm-' + source(selected)['sha256']
