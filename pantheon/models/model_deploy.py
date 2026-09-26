"""Deploy a model: where (a new Modal machine or an existing node), which engine, which model.

Engines: SGLang (node-provided, NVIDIA GPU; Modal machines) serving a catalog or a
custom Hugging Face model pinned to an exact revision and per-file sha256, and
Ollama (any Linux/Windows/macOS node, GPU or CPU) serving a verified GGUF.
Everything below reuses Model Services: managed deployments, verified downloads,
publication with the model's capabilities, and for Modal machines a stable route
`fleet-route://<service id>`.

`deploy` records a small plan (engine, model, target) so `status` can drive the
next idempotent step after the Agent restarts; the UI calls `status` repeatedly.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re

import httpx

from . import modal_gpu

# GPU memory by Modal GPU type; node GPUs report their own totals.
GPU_MEMORY = {'H100': 80 << 30, 'A100-80GB': 80 << 30, 'L40S': 45 << 30}  # L40S: 48 GB ≈ 45 GiB
MODAL_MEMORY = {True: 64 << 30, False: 16 << 30}  # launch defaults with / without a GPU
OLLAMA_VERSION = '0.34.2'
HF = 'https://huggingface.co'
OLLAMA = 'https://ollama.com'
OLLAMA_REGISTRY = 'https://registry.ollama.ai'
_tasks = {}  # deployment_id -> background engine start (this Agent process)


def _slug(text, limit=36):
    return re.sub('[^a-z0-9-]+', '-', str(text).lower()).strip('-')[:limit].strip('-') or 'model'


def _plans():
    directory = Path(os.environ.get('PANTHEON_MODEL_DEPLOY_DIR') or Path.home() / '.pantheon' / 'model-deploy')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _save_plan(plan):
    path = _plans() / (plan['deployment_id'] + '.json')
    path.write_text(json.dumps(plan, sort_keys=True))


def _load_plan(deployment_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', str(deployment_id)):
        raise ValueError('Invalid deployment id')
    path = _plans() / (deployment_id + '.json')
    return json.loads(path.read_text()) if path.is_file() else None


def llm_catalog():
    from .managed import module
    return module('llm_models').catalog()


def ollama_catalog():
    from pantheon.apps.registry import BUILTIN_ROOT
    return json.loads((BUILTIN_ROOT / 'model-service' / 'ollama-models.json').read_text())['models']


def _ollama_model(model):
    if isinstance(model, dict) and model.get('catalog_id'):
        selected = next((m for m in ollama_catalog() if m['id'] == model['catalog_id']), None)
        if not selected:
            raise ValueError('Choose an Ollama catalog model')
        return selected
    if not isinstance(model, dict) or not re.fullmatch(r'(gguf|ollama)-[a-z0-9][a-z0-9.-]{0,90}', str(model.get('id', ''))):
        raise ValueError('Choose an Ollama model or resolve a GGUF file first')
    source = model.get('source') or {}
    if (source.get('format') != 'gguf' or not re.fullmatch('[a-f0-9]{64}', str(source.get('sha256', '')))
            or type(source.get('size')) is not int
            or not str(source.get('url', '')).startswith((HF + '/', OLLAMA_REGISTRY + '/v2/'))):
        raise ValueError('A custom model needs a pinned Hugging Face or Ollama registry URL, sha256 and size')
    if model.get('template') is not None or model.get('parameters') is not None:
        _import_settings(model.get('template') or '', model.get('parameters') or {})
    return model


def _import_settings(template, parameters):
    """Same bounds the connector enforces before /api/create."""
    from .managed import module
    return module('model_control').ModelControl.import_settings(template, parameters)


def _sglang_model(model):
    from .managed import module
    llm = module('llm_models')
    if isinstance(model, dict) and model.get('catalog_id'):
        return llm.model(model['catalog_id']), None
    entry = llm.model(model)  # validates a resolved custom entry
    return entry, entry


# -- where: describe the target -------------------------------------------------------

async def _target(manager, node_id='', gpu=''):
    """Normalised target facts: platform, gpu name/memory, system memory, Modal-ness."""
    if node_id:
        nodes = await manager.resolver._list_nodes(max_age=0)
        node = next((n for n in nodes if n['node_id'] == node_id), None)
        if not node:
            raise ValueError('That node is not online in your Fleet')
        cap = node.get('capability') or {}
        labels = node.get('labels') or []
        service = next((label[4:] for label in labels if label.startswith('svc-')), '')
        launch = next((s for s in await modal_gpu.services(manager) if s['service_id'] == service), None) if service else None
        accelerators = ((cap.get('resources') or {}).get('accelerators') or [])
        cuda = next((a for a in accelerators if a.get('backend') == 'cuda'), None)
        metal = next((a for a in accelerators if a.get('backend') == 'metal'), None)
        memory = (launch or {}).get('memory_gib')
        return dict(node_id=node_id, node=node, platform=f"{cap.get('os')}-{cap.get('arch')}",
                    modal='modal' in labels or 'modal-gpu' in labels, service_id=service,
                    gpu_name=(cuda or {}).get('name') or (launch or {}).get('gpu') or '',
                    gpu_memory=((cuda or {}).get('memory') or {}).get('total_bytes') or 0,
                    gpu_id=(cuda or {}).get('id', ''), metal=bool(metal),
                    memory=(memory << 30) if memory else int((cap.get('ram_gb') or 0) * (1 << 30)))
    if gpu not in modal_gpu.NODE_GPUS:
        raise ValueError('Choose a node or a Modal GPU (H100, A100-80GB, L40S or none)')
    has_gpu = gpu != 'none'
    return dict(node_id='', platform='linux-amd64', modal=True, service_id='', gpu_name=gpu if has_gpu else '',
                gpu_memory=GPU_MEMORY.get(gpu, 0), gpu_id='', metal=False, memory=MODAL_MEMORY[has_gpu])


def _gib(value):
    return f'{value / (1 << 30):.0f} GB'


def _sglang_fit(entry, target):
    if not target['gpu_name']:
        return False, 'Needs an NVIDIA GPU'
    if message := modal_gpu._gpu_mismatch(entry, target['gpu_name']):
        return False, 'Needs ' + ' or '.join(entry.get('supported_gpus') or [])
    if target['gpu_memory'] and target['gpu_memory'] * 9 // 10 < entry['minimum_gpu_memory_bytes']:
        return False, f"Needs {_gib(entry['minimum_gpu_memory_bytes'])} GPU memory"
    if target['memory'] and target['memory'] < entry['minimum_memory_bytes']:
        return False, f"Needs {_gib(entry['minimum_memory_bytes'])} system memory"
    return True, ''


def _ollama_fit(entry, target):
    need = entry['min_memory_bytes']
    if target['gpu_memory'] and target['gpu_memory'] * 9 // 10 >= need:
        return True, ''
    if target['memory'] and target['memory'] * 3 // 4 >= need:
        return True, 'Runs on CPU' if not target['gpu_name'] and not target['metal'] else ''
    return False, f'Needs about {_gib(need)} of memory'


async def options(manager, node_id='', gpu=''):
    target = await _target(manager, node_id, gpu)
    sglang_ok = bool(target['gpu_name']) and target['modal'] and target['platform'] == 'linux-amd64'
    ollama_ok = target['platform'] in {'linux-amd64', 'linux-arm64', 'windows-amd64', 'windows-arm64',
                                       'darwin-arm64', 'darwin-amd64'}
    engines = [
        dict(id='sglang', label='SGLang', description='Fast serving on an NVIDIA GPU; full-precision or FP8 Hugging Face models.',
             available=sglang_ok, reason='' if sglang_ok else (
                 'Needs an NVIDIA GPU' if not target['gpu_name'] else 'Runs on platform GPU machines (Modal)')),
        dict(id='ollama', label='Ollama', description='Quantized GGUF models on GPU or CPU; any machine.',
             available=ollama_ok, reason='' if ollama_ok else 'Not available for this platform'),
        dict(id='lmstudio', label='LM Studio', description='Your LM Studio app on a Mac.',
             available=False, reason='Set up LM Studio from Advanced → Add service'),
    ]
    sglang_models = []
    for entry in llm_catalog():
        fits, reason = _sglang_fit(entry, target) if sglang_ok else (False, engines[0]['reason'])
        sglang_models.append(dict(id=entry['id'], display_name=entry['display_name'],
                                  size=sum(f['size'] for f in entry['files']), context_length=entry['context_length'],
                                  maximum_context_length=entry['maximum_context_length'],
                                  supported_gpus=entry.get('supported_gpus') or [], capabilities=entry['capabilities'],
                                  fits=fits, reason=reason))
    ollama_models = []
    for entry in ollama_catalog():
        fits, reason = _ollama_fit(entry, target) if ollama_ok else (False, engines[1]['reason'])
        ollama_models.append(dict(id=entry['id'], display_name=entry['display_name'], size=entry['source']['size'],
                                  min_memory_bytes=entry['min_memory_bytes'], context_length=entry['context_length'],
                                  maximum_context_length=entry.get('maximum_context_length') or entry['context_length'],
                                  capabilities=entry['capabilities'], gpu_optional=True, fits=fits, reason=reason))
    return dict(target={k: target[k] for k in ('node_id', 'platform', 'modal', 'gpu_name', 'gpu_memory', 'memory')},
                engines=engines, sglang_models=sglang_models, ollama_models=ollama_models)


# -- resolve: pin a custom Hugging Face model -----------------------------------------

async def _hf_json(client, path):
    response = await client.get(HF + path)
    if response.status_code == 404:
        raise ValueError('Hugging Face repository or revision not found (private and gated models are not supported)')
    if response.status_code in {401, 403}:
        raise ValueError('This Hugging Face model is gated or private; only public models can be pinned')
    response.raise_for_status()
    return response.json()


def _parsers(config, repo):
    kind = str(config.get('model_type') or (config.get('text_config') or {}).get('model_type') or '').lower()
    name = repo.lower()
    if kind.startswith(('qwen3_5', 'qwen3_next')):
        return 'qwen3_coder', 'qwen3'
    if kind.startswith('qwen3'):
        # Instruct-2507 releases are non-thinking; other Qwen3 checkpoints think.
        return 'qwen25', '' if ('instruct' in name and '2507' in name) else 'qwen3'
    if kind.startswith('qwen2'):
        return 'qwen25', ''
    if kind.startswith('llama'):
        return 'llama3', ''
    if kind.startswith('mistral'):
        return 'mistral', ''
    if kind.startswith('deepseek_v3'):
        return 'deepseekv3', ''
    return '', ''


async def resolve_hf(repo, revision='', *, client=None):
    """Pin a public Hugging Face safetensors model for the node-provided SGLang."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', str(repo)):
        raise ValueError('Enter a Hugging Face repository like Qwen/Qwen3-8B')
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        info = await _hf_json(client, f'/api/models/{repo}/revision/{revision or "main"}?blobs=true')
        sha = info['sha']
        config_response = await client.get(f'{HF}/{repo}/resolve/{sha}/config.json')
        config_response.raise_for_status()
        config = config_response.json()
        if config.get('auto_map'):
            raise ValueError('This model needs custom code (trust_remote_code), which is not allowed')
        files = []
        for sibling in sorted(info['siblings'], key=lambda s: s['rfilename']):
            name = sibling['rfilename']
            if '/' in name or not (name == 'LICENSE' or name.endswith(('.safetensors', '.json', '.txt', '.md', '.jinja'))):
                continue
            url = f'{HF}/{repo}/resolve/{sha}/{name}'
            if sibling.get('lfs'):
                digest, size = sibling['lfs']['sha256'], sibling['lfs']['size']
            else:
                body = await client.get(url)
                body.raise_for_status()
                digest, size = hashlib.sha256(body.content).hexdigest(), len(body.content)
            files.append(dict(name=name, url=url, size=size, sha256=digest, format='data', revision=sha))
    finally:
        if own:
            await client.aclose()
    weights = sum(f['size'] for f in files if f['name'].endswith('.safetensors'))
    if not weights:
        raise ValueError('Only safetensors models can be served by SGLang here')
    text = config.get('text_config') or {}
    context = int(config.get('max_position_embeddings') or text.get('max_position_embeddings') or 32768)
    quant = str((config.get('quantization_config') or text.get('quantization_config') or {}).get('quant_method', '')).lower()
    minimum_gpu = weights * 11 // 10 + (6 << 30)
    gpus = ['H100', 'L40S'] if quant == 'fp8' else ['A100', 'H100', 'L40S']
    gpus = [g for g in gpus if (GPU_MEMORY['L40S'] if g == 'L40S' else GPU_MEMORY['H100']) * 9 // 10 >= minimum_gpu]
    if not gpus:
        raise ValueError(f'This model needs about {_gib(minimum_gpu)} of GPU memory, more than one GPU offers')
    tool, reasoning = _parsers(config, repo)
    card = info.get('cardData') or {}
    license_ = str(card.get('license') or 'see model card')[:64]
    entry = dict(id=f"hf-{_slug(repo.replace('/', '-'), 70)}-{sha[:8]}", model=repo, revision=sha,
                 operation='text', license=license_, display_name=repo.split('/')[-1][:120],
                 context_length=min(context, 32768), maximum_context_length=min(context, 1048576),
                 minimum_memory_bytes=min(48 << 30, max(16 << 30, weights // 2)), minimum_gpu_memory_bytes=minimum_gpu,
                 tool_call_parser=tool, reasoning_parser=reasoning, supported_gpus=gpus,
                 capabilities=dict(tools=bool(tool), reasoning=bool(reasoning), vision=False), files=files)
    from .managed import module
    module('llm_models').model(entry)  # same validation as catalog entries
    warnings = []
    if not tool:
        warnings.append('No tool-call parser is known for this model family; it can chat but not use tools.')
    if quant and quant != 'fp8':
        warnings.append(f'Quantization {quant} is served as-is by SGLang; check the model card.')
    return dict(model=entry, warnings=warnings, size=sum(f['size'] for f in files))


async def resolve_gguf(repo, file, revision='', *, client=None):
    """Pin one public GGUF file for Ollama."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', str(repo)) or not str(file).endswith('.gguf') or '/' in str(file):
        raise ValueError('Enter a Hugging Face repository and a .gguf file name')
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        info = await _hf_json(client, f'/api/models/{repo}/revision/{revision or "main"}?blobs=true')
    finally:
        if own:
            await client.aclose()
    sibling = next((s for s in info['siblings'] if s['rfilename'] == file), None)
    if not sibling or not sibling.get('lfs'):
        raise ValueError('That GGUF file is not in the repository')
    size, sha = sibling['lfs']['size'], info['sha']
    family = repo.lower()
    tools = any(k in family for k in ('qwen2.5', 'qwen3', 'llama-3', 'mistral'))
    entry = dict(id=f"gguf-{_slug(file.removesuffix('.gguf'), 60)}-{sibling['lfs']['sha256'][:8]}",
                 display_name=file.removesuffix('.gguf')[:120], repo=repo,
                 license=str((info.get('cardData') or {}).get('license') or 'see model card')[:64],
                 context_length=8192, capabilities=dict(tools=tools, reasoning=False, vision=False),
                 min_memory_bytes=int(size * 1.2) + (2 << 30),
                 source=dict(url=f'{HF}/{repo}/resolve/{sha}/{file}', sha256=sibling['lfs']['sha256'],
                             size=size, name=file, revision=sha, format='gguf'))
    return dict(model=entry, warnings=[] if tools else ['Tool use depends on the GGUF chat template; it may only chat.'],
                size=size)


def sglang_architectures():
    from pantheon.apps.registry import BUILTIN_ROOT
    return set(json.loads((BUILTIN_ROOT / 'model-service' / 'sglang-architectures.json').read_text())['architectures'])


DTYPE_BYTES = {'F64': 8, 'F32': 4, 'I32': 4, 'U32': 4, 'BF16': 2, 'F16': 2, 'I16': 2,
               'F8_E4M3': 1, 'F8_E5M2': 1, 'I8': 1, 'U8': 1}
HF_SORTS = {'popular': 'downloads', 'trending': 'trendingScore', 'likes': 'likes', 'recent': 'lastModified'}


def _weights_bytes(safetensors):
    """Weight size from Hugging Face's per-dtype parameter counts."""
    counts = (safetensors or {}).get('parameters') or {}
    return sum(DTYPE_BYTES.get(dtype, 2) * n for dtype, n in counts.items() if type(n) is int)


async def search_hf(query, limit=20, gpu='', *, sort='popular', gpu_memory=0, client=None):
    """Chat models on Hugging Face in safetensors format, marked by what the pinned
    SGLang can serve and whether the weights fit the target GPU."""
    params = ([('search', query)] if query else []) + [
        ('pipeline_tag', 'text-generation'), ('filter', 'conversational'), ('filter', 'safetensors'),
        ('sort', HF_SORTS.get(sort, 'downloads')), ('direction', '-1'),
        ('limit', str(max(1, min(int(limit) * 2, 60))))]
    params += [('expand[]', k) for k in ('config', 'downloads', 'likes', 'lastModified', 'safetensors', 'gated', 'tags')]
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        response = await client.get(HF + '/api/models', params=params)
        response.raise_for_status()
        rows = response.json()
    finally:
        if own:
            await client.aclose()
    supported_archs = sglang_architectures()
    gpu_memory = gpu_memory or GPU_MEMORY.get(gpu, 0)
    results = []
    for row in rows:
        config = row.get('config') or {}
        archs = config.get('architectures') or []
        quant = str((config.get('quantization_config') or {}).get('quant_method') or '').lower()
        total = (row.get('safetensors') or {}).get('total')
        # Test fixtures and toy checkpoints are not useful chat models.
        if type(total) is int and total < 100_000_000:
            continue
        weights = _weights_bytes(row.get('safetensors'))
        # Weights plus KV cache, activations and CUDA graphs for a modest context.
        need = int(weights * 1.2) + (3 << 30) if weights else 0
        reason = ''
        if row.get('gated'):
            reason = 'Gated on Hugging Face; only public models can be pinned'
        elif not archs:
            reason = 'No model architecture declared'
        elif not set(archs) & supported_archs:
            reason = f'SGLang 0.5.20 does not serve {archs[0]}'
        elif config.get('auto_map'):
            reason = 'Needs custom code (trust_remote_code)'
        elif quant in {'modelopt', 'modelopt_fp4', 'nvfp4', 'mxfp4'} and 'fp8' not in str(config.get('quantization_config')).lower():
            reason = f'{quant} (FP4) needs Blackwell GPUs'
        elif quant in {'gptq', 'awq', 'bitsandbytes'}:
            reason = f'{quant.upper()} quantized weights are not supported here; choose the original or FP8 model'
        elif quant == 'fp8' and gpu and gpu not in {'H100', 'L40S'}:
            reason = 'FP8 needs H100 or L40S'
        fits = None
        if gpu_memory and need:
            fits = need <= gpu_memory * 9 // 10
            if not fits and not reason:
                reason = f'Needs about {_gib(need)} GPU memory'
        tool_parser, reasoning_parser = _parsers(config, row['id'])
        results.append(dict(id=row['id'], downloads=row.get('downloads', 0), likes=row.get('likes', 0),
                            updated=row.get('lastModified'), architecture=archs[0] if archs else '',
                            quantization=quant, parameters=total, weights_bytes=weights or None,
                            gpu_memory_needed=need or None, fits=fits,
                            tools=bool(tool_parser), reasoning=bool(reasoning_parser),
                            supported=not reason, reason=reason))
        if len(results) >= int(limit):
            break
    return results


def _parse_ollama_search(html_text, limit):
    import html
    results = []
    for block in re.findall(r'<li[^>]*>\s*<a href="/library/([^"]+)"(.*?)</li>', html_text, re.S)[:limit]:
        name, body = block
        description = re.search(r'<p[^>]*>([^<]+)</p>', body)
        chips = [html.unescape(c).strip() for c in re.findall(r'<span[^>]*rounded-md[^>]*>([^<]+)</span>', body)]
        sizes = [c for c in chips if re.fullmatch(r'[0-9.]+[bmk](-[a-z0-9]+)?|[0-9]+x[0-9.]+b|e[0-9]+b', c.lower())]
        capabilities = [c for c in chips if c.lower() in {'tools', 'thinking', 'vision', 'embedding', 'cloud', 'audio'}]
        pulls = re.search(r'x-test-pull-count[^>]*>([^<]+)<', body)
        results.append(dict(name=name, description=html.unescape(description.group(1).strip()) if description else '',
                            tags=sizes, capabilities=capabilities, pulls=pulls.group(1) if pulls else '',
                            cloud_only='cloud' in capabilities and not sizes))
    return results


async def search_ollama(query, limit=20, *, client=None):
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        # An empty query lists the library by popularity.
        response = await client.get(OLLAMA + '/search', params={'q': query} if query else {'o': 'popular'})
        response.raise_for_status()
        return _parse_ollama_search(response.text, max(1, min(int(limit), 50)))
    finally:
        if own:
            await client.aclose()


async def _on_machine(manager, node_id):
    """Models already served by an Ollama or LM Studio service on this node."""
    found = []
    for row in await manager.client.deployments():
        if row.get('node_id') != node_id or row.get('engine') not in {'ollama', 'lmstudio'} or row['state'] != 'ready':
            continue
        try:
            models = (await manager.discover(row['deployment_id'])).get('models', [])
        except Exception:
            models = row.get('models') or []
        for m in models:
            found.append(dict(name=m.get('name') or m['id'], id=m['id'], deployment_id=row['deployment_id'],
                              engine=row['engine'], source='on_machine', published=any(
                                  p['id'] == m['id'] for p in row.get('models') or [])))
    return found


async def search(manager, engine, query='', node_id='', gpu='', limit=20, sort='popular'):
    if engine == 'sglang':
        target = await _target(manager, node_id, gpu) if (node_id or gpu) else {}
        return dict(engine='sglang', sort=sort, results=await search_hf(
            query, limit, target.get('gpu_name', gpu), sort=sort, gpu_memory=target.get('gpu_memory', 0)))
    if engine == 'ollama':
        results = await search_ollama(query, limit)
        machine = await _on_machine(manager, node_id) if node_id else []
        if query:
            machine = [m for m in machine if query.lower() in m['name'].lower()]
        return dict(engine='ollama', results=results, on_machine=machine)
    raise ValueError('Search SGLang (Hugging Face) or Ollama models')


def _ollama_ref(ref):
    match = re.fullmatch(r'(?:([a-z0-9][a-z0-9._-]{0,63})/)?([a-z0-9][a-z0-9._-]{0,95})(?::([A-Za-z0-9][A-Za-z0-9._-]{0,127}))?', str(ref))
    if not match:
        raise ValueError('Enter an Ollama model like qwen3:8b')
    namespace, name, tag = match.groups()
    return namespace or 'library', name, tag or 'latest'


async def resolve_ollama(ref, *, client=None):
    """Pin an Ollama library model (name:tag) to its manifest: verified GGUF layer, template and parameters."""
    namespace, name, tag = _ollama_ref(ref)
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    base = f'{OLLAMA_REGISTRY}/v2/{namespace}/{name}'
    try:
        response = await client.get(f'{base}/manifests/{tag}',
                                    headers={'Accept': 'application/vnd.docker.distribution.manifest.v2+json'})
        if response.status_code == 404:
            raise ValueError(f'{name}:{tag} is not in the Ollama library')
        response.raise_for_status()
        manifest_digest = hashlib.sha256(response.content).hexdigest()
        manifest = response.json()
        layers = {layer['mediaType'].rsplit('.', 1)[-1]: layer for layer in manifest.get('layers', [])}
        model = layers.get('model')
        if not model:
            raise ValueError('This Ollama model has no local weights (cloud-only models cannot be deployed)')
        texts = {}
        for kind in ('template', 'params'):
            layer = layers.get(kind)
            if not layer:
                continue
            if layer['size'] > 64 << 10:
                raise ValueError(f'Unexpectedly large {kind} layer')
            blob = await client.get(f"{base}/blobs/{layer['digest']}")
            blob.raise_for_status()
            if 'sha256:' + hashlib.sha256(blob.content).hexdigest() != layer['digest']:
                raise ValueError(f'The {kind} layer does not match its digest')
            texts[kind] = blob.content.decode()
    finally:
        if own:
            await client.aclose()
    raw = json.loads(texts['params']) if 'params' in texts else {}
    parameters = {k: v for k, v in raw.items()
                  if k in {'stop', 'temperature', 'top_p', 'top_k', 'min_p', 'repeat_penalty', 'presence_penalty', 'repeat_last_n'}}
    template = texts.get('template', '')
    _import_settings(template, parameters)
    digest = model['digest'].removeprefix('sha256:')
    size = model['size']
    display = f'{name}:{tag}' if namespace == 'library' else f'{namespace}/{name}:{tag}'
    entry = dict(id=f"ollama-{_slug(display.replace(':', '-').replace('/', '-'), 70)}-{digest[:8]}", display_name=display,
                 repo=f'ollama:{display}', license='see ollama.com/library/' + name,
                 context_length=int(raw.get('num_ctx') or 8192) if int(raw.get('num_ctx') or 8192) <= 131072 else 8192,
                 capabilities=dict(tools='.Tools' in template, reasoning='.Think' in template or 'think' in template.lower(),
                                   vision='projector' in layers),
                 min_memory_bytes=int(size * 1.2) + (2 << 30), template=template, parameters=parameters,
                 source=dict(url=f'{base}/blobs/sha256:{digest}', sha256=digest, size=size,
                             name=f'{_slug(display, 80)}.gguf', revision=manifest_digest, format='gguf'))
    warnings = ['Vision projector layers are not imported; the model runs text-only.'] if 'projector' in layers else []
    if 'projector' in layers:
        entry['capabilities']['vision'] = False
    return dict(model=entry, warnings=warnings, size=size)


async def resolve(engine, repo, revision='', file=''):
    if engine == 'sglang':
        return await resolve_hf(repo, revision)
    if engine == 'ollama':
        if file:
            return await resolve_gguf(repo, file, revision)
        return await resolve_ollama(repo if not revision else f'{repo}:{revision}')
    raise ValueError('Choose SGLang or Ollama')


# -- deploy / status ------------------------------------------------------------------

async def deploy(manager, target, engine, model, name='', context_length=None):
    """Start a deployment; returns its id and first status."""
    if not isinstance(target, dict) or target.get('kind') not in {'modal', 'node'}:
        raise ValueError('Choose a new Modal machine or one of your nodes')
    if engine == 'sglang':
        entry, custom = _sglang_model(model)
        label = entry['display_name']
    elif engine == 'ollama':
        entry, custom = _ollama_model(model), None
        label = entry['display_name']
    else:
        raise ValueError('Choose SGLang or Ollama')
    plan = dict(engine=engine, name=(name or label)[:120], model=model if not isinstance(model, dict) or not model.get('catalog_id')
                else {'catalog_id': model['catalog_id']})
    if context_length is not None:
        maximum = entry.get('maximum_context_length') or entry.get('context_length')
        if type(context_length) is not int or not 512 <= context_length <= maximum:
            raise ValueError(f'Context must be between 512 and {maximum:,} tokens for this model')
        plan['context_length'] = context_length
    if target['kind'] == 'modal':
        gpu = target.get('gpu', 'H100')
        facts = await _target(manager, gpu=gpu)
        fits, reason = (_sglang_fit(entry, facts) if engine == 'sglang' else _ollama_fit(entry, facts))
        if engine == 'sglang' and gpu == 'none':
            raise ValueError('SGLang needs a GPU')
        if not fits:
            raise ValueError(reason)
        # node-* ids: the machine is launched bare and this plan deploys onto it,
        # so nothing else auto-advances it as a legacy model service.
        service_id = modal_gpu.node_service_id(name or label)
        hours = float(target.get('lifetime_hours', 4))
        if not 0 < hours <= 24:
            raise ValueError('Time limit must be between 0 and 24 hours')
        sizes = {k: target[k] for k in ('cpu', 'memory_gib') if target.get(k) is not None}
        if not any(s['service_id'] == service_id for s in await modal_gpu.services(manager)):
            token = (await modal_gpu._controller('/join-tokens', {}))['join_token']
            try:
                await manager.client.hub_request('POST', '/api/model-services/modal-gpu', dict(
                    service_id=service_id, gpu=gpu, join_token=token, lifetime_minutes=int(hours * 60), **sizes))
            finally:
                del token
        plan.update(service_id=service_id, deployment_id=modal_gpu.deployment_id(service_id))
    else:
        facts = await _target(manager, node_id=target.get('node_id', ''))
        fits, reason = (_sglang_fit(entry, facts) if engine == 'sglang' else _ollama_fit(entry, facts))
        if engine == 'sglang' and not (facts['modal'] and facts['gpu_name']):
            raise ValueError('SGLang runs on platform GPU machines (Modal)')
        if not fits:
            raise ValueError(reason)
        if facts['service_id']:
            plan.update(service_id=facts['service_id'], deployment_id=modal_gpu.deployment_id(facts['service_id']))
        else:
            plan.update(node_id=facts['node_id'], deployment_id=f"{engine}-{_slug(name or label, 50)}")
    _save_plan(plan)
    return await status(manager, plan['deployment_id'])


async def status(manager, deployment_id):
    plan = _load_plan(deployment_id)
    if plan is None:
        row = next((r for r in await manager.client.deployments() if r['deployment_id'] == deployment_id), None)
        if not row:
            raise ValueError('Unknown deployment')
        return dict(deployment_id=deployment_id, phase='ready' if row['state'] == 'ready' else row['state'],
                    ready=row['state'] == 'ready')
    base = dict(deployment_id=deployment_id, engine=plan['engine'], name=plan['name'],
                service_id=plan.get('service_id'), route=f"fleet-route://{plan['service_id']}" if plan.get('service_id') else None)
    if plan['engine'] == 'sglang':
        model = plan['model']
        catalog_id = model.get('catalog_id') if isinstance(model, dict) else None
        result = await modal_gpu.advance(manager, plan['service_id'], catalog_id or 'qwen3.6-35b-a3b-fp8',
                                         model=None if catalog_id else model,
                                         context_length=plan.get('context_length'))
        return {**base, **{k: v for k, v in result.items() if k not in base or v is not None}}
    return {**base, **await _advance_ollama(manager, plan)}


def _ollama_recipe(platform):
    system, arch = platform.split('-')
    return f'ollama-{OLLAMA_VERSION}-darwin' if system == 'darwin' else f'ollama-{OLLAMA_VERSION}-{platform}'


async def _advance_ollama(manager, plan):
    entry = _ollama_model(plan['model'])
    dep = plan['deployment_id']
    if plan.get('service_id'):
        launched = next((s for s in await modal_gpu.services(manager) if s['service_id'] == plan['service_id']), None)
        if not launched:
            return dict(phase='stopped', ready=False)
        node = modal_gpu._node_for(await manager.resolver._list_nodes(max_age=0), plan['service_id'])
        if not node:
            return dict(phase='starting_node', ready=False)
        node_id = node['node_id']
    else:
        node_id = plan['node_id']
    rows = {r['deployment_id']: r for r in await manager.client.deployments()}
    row = rows.get(dep)
    if row is None:
        facts = await _target(manager, node_id=node_id)
        need = entry['min_memory_bytes']
        if facts['gpu_id'] and facts['gpu_memory'] * 9 // 10 >= need:
            devices = [dict(id=facts['gpu_id'], backend='cuda', memory_bytes=min(facts['gpu_memory'] * 9 // 10, max(need, 4 << 30)),
                            exclusive=False)]
            memory = 8 << 30
        elif facts['metal']:
            memory = max(need, 4 << 30)
            devices = [dict(id='apple-metal', backend='metal', memory_bytes=memory, exclusive=False)]
        else:
            devices, memory = [], max(need, 2 << 30)
        config = dict(recipe_id=_ollama_recipe(facts['platform']), context_length=plan.get('context_length') or entry['context_length'], parallel=1,
                      keep_alive_seconds=1800, load_policy='warm', resources=dict(memory_bytes=memory, devices=devices))
        row = await manager.create_managed(dep, plan['name'], node_id, config)
    if row['state'] != 'ready':
        recipes = (await manager.engines(dep))['recipes']
        recipe = next((r for r in recipes if r['id'] == row['managed']['recipe_id']), {})
        if not recipe.get('prepared'):
            jobs = (await manager.engines(dep, 'jobs'))['jobs']
            job = next((j for j in jobs if j.get('job_id') == recipe.get('id')), None)
            if not job or job.get('state') in {'failed', 'cancelled'}:
                await manager.engines(dep, 'prepare', recipe_id=row['managed']['recipe_id'], resume=True)
            return dict(phase='preparing_engine', ready=False,
                        progress=dict(bytes=(job or {}).get('bytes_done', 0), total=(recipe.get('source') or {}).get('size', 0),
                                      error=(job or {}).get('error', '')))
        task = _tasks.get(dep)
        if task is None or task.done():
            if task is not None and task.exception():
                _tasks.pop(dep)
                return dict(phase='failed', ready=False, error=str(task.exception())[:300])
            _tasks[dep] = asyncio.create_task(manager.set_running(dep, True))
        return dict(phase='starting_engine', ready=False)
    job_id = 'weights-' + entry['source']['sha256'][:16]
    jobs = (await manager.artifacts(dep))['jobs']
    job = next((j for j in jobs if j.get('job_id') == job_id), None)
    if not job or job.get('state') in {'failed', 'cancelled'}:
        await manager.artifacts(dep, 'submit', job_id, entry['source'], resume=True)
    if not job or job.get('state') != 'ready':
        return dict(phase='downloading_weights', ready=False,
                    progress=dict(bytes=(job or {}).get('bytes_done', 0), total=entry['source']['size'],
                                  error=(job or {}).get('error', '')))
    model_id = 'fleet/' + entry['source']['sha256'] + ':latest'
    if not any(m['id'] == model_id for m in row.get('models') or []):
        state = await manager.model_operations(dep)
        if not any(m['id'] == model_id for m in state['models']):
            import_id = 'import-' + entry['source']['sha256'][:16]
            existing = next((j for j in state.get('jobs', []) if j['job_id'] == import_id), None)
            if existing and existing['state'] == 'failed':
                return dict(phase='failed', ready=False, error=existing.get('error') or 'Model import failed')
            if not existing:
                await manager.model_operations(dep, 'submit', job_id=import_id, operation='import', artifact_job_id=job_id,
                                               template=entry.get('template') or '', parameters=entry.get('parameters') or None)
            return dict(phase='publishing', ready=False)
        caps = entry['capabilities']
        row = await manager.publish(dep, [dict(id=model_id, name=entry['display_name'], operations=['text'],
                                               tools=caps['tools'], reasoning=caps['reasoning'], vision=caps['vision'],
                                               context=row['managed']['context_length'], compute='node')], row['revision'])
    if plan.get('service_id'):
        routes = {r['route_id']: r for r in await manager.client.routes()}
        route = routes.get(plan['service_id'])
        candidate = dict(deployment_id=dep, model_id=model_id)
        if not route or route['candidates'] != [candidate] or route['allowed_nodes'] != [node_id]:
            await manager.client.route_operation('save', route=dict(
                route_id=plan['service_id'], name=entry['display_name'] + ' (Modal)', candidates=[candidate],
                allowed_nodes=[node_id], requires=dict(operation='text', tools=entry['capabilities']['tools'],
                                                       context=row['managed']['context_length']),
                revision=route['revision'] if route else 0))
    from .client import model_ref
    return dict(phase='ready', ready=True, node_id=node_id, model=model_ref(dep, model_id))
