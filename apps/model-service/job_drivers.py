"""Explicit typed adapters; caller input can never choose an endpoint or path.

SGLang's /v1/rerank is not an official OpenAI endpoint. Its list response and
common API-connector results/relevance_score responses are normalized below.
Other modalities require their own validated adapters, not chat-completion casts.
"""
import json
import math
import re


SPEECH_OUTPUT_LIMIT = 64 * 1024 * 1024


def prepare(config, body):
    if (not isinstance(body, dict) or set(body) != {'job_id', 'model', 'operation', 'input', 'parameters'}
            or not isinstance(body['model'], str) or not 0 < len(body['model']) <= 200
            or body['operation'] not in {'rerank', 'speech', 'transcription', 'image'}):
        raise ValueError('This typed operation is not supported by the configured engine adapter')
    if body['operation'] == 'image':
        # Imported by the connector's module loader; no engine dependencies here.
        return prepare_image(config, body)
    if body['operation'] == 'speech':
        return prepare_speech(config, body)
    if body['operation'] == 'transcription':
        return prepare_transcription(config, body)
    if config.get('engine') not in {'sglang', 'api'}:
        raise ValueError('Rerank requires the SGLang or API connector adapter')
    inputs, params = body['input'], body['parameters']
    if (not isinstance(inputs, dict) or set(inputs) != {'query', 'documents'}
            or not isinstance(inputs['query'], str) or not inputs['query'].strip()
            or not isinstance(inputs['documents'], list) or not 1 <= len(inputs['documents']) <= 128
            or any(not isinstance(d, str) or not d.strip() for d in inputs['documents'])
            or not isinstance(params, dict) or set(params) - {'top_n', 'return_documents'}):
        raise ValueError('Rerank needs a query, documents and supported parameters')
    top_n = params.get('top_n', len(inputs['documents']))
    if (type(top_n) is not int or not 1 <= top_n <= len(inputs['documents'])
            or type(params.get('return_documents', False)) is not bool):
        raise ValueError('Invalid rerank result count or document option')
    # The driver reconstructs requested documents by index. An upstream cannot
    # inject unbounded documents/URLs into the persisted result.
    return {'path': '/rerank', 'payload': {'model': body['model'], **inputs,
            'top_n': top_n, 'return_documents': False}, 'inputs': [],
            'return_documents': params.get('return_documents', False)}


def prepare_image(config, body):
    inputs, params = body['input'], body['parameters']
    if (config.get('engine') != 'sglang' or config.get('managed')
            or not isinstance(inputs, dict) or set(inputs) != {'text'}
            or not isinstance(inputs['text'], str) or not inputs['text'].strip()
            or len(inputs['text']) > 32768 or not isinstance(params, dict)
            or set(params) - {'size', 'seed', 'num_inference_steps', 'guidance_scale', 'negative_prompt', 'n', 'output_format'}):
        raise ValueError('Image generation needs an attached SGLang Diffusion engine and supported parameters')
    size = params.get('size', '1024x1024')
    if not isinstance(size, str) or not re.fullmatch('[0-9]{2,4}x[0-9]{2,4}', size):
        raise ValueError('Use an explicit image size')
    width, height = map(int, size.split('x'))
    if any(v < 64 or v > 2048 or v % 8 for v in (width, height)):
        raise ValueError('Image dimensions must be multiples of 8 between 64 and 2048')
    for key, low, high in [('seed', 0, 2**32-1), ('num_inference_steps', 1, 100)]:
        if key in params and (type(params[key]) is not int or not low <= params[key] <= high):
            raise ValueError('Invalid image sampling parameter')
    scale = params.get('guidance_scale', 1)
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 <= scale <= 30:
        raise ValueError('Invalid image guidance scale')
    if ('negative_prompt' in params and (not isinstance(params['negative_prompt'], str)
            or len(params['negative_prompt']) > 32768)):
        raise ValueError('Invalid negative prompt')
    if type(params.get('n', 1)) is not int or params.get('n', 1) != 1 or params.get('output_format', 'png') != 'png':
        raise ValueError('This image adapter produces one PNG per job')
    return {'path': '/images/generations', 'inputs': [], 'driver': 'diffusion_image',
            'payload': {'model': body['model'], 'prompt': inputs['text'], **params,
                        'size': size, 'n': 1, 'output_format': 'png', 'response_format': 'url'},
            'output': {'kind': 'image', 'mime': 'image/png', 'max_size': 32 * 1024 * 1024}}


def prepare_transcription(config, body):
    inputs, params = body['input'], body['parameters']
    if (config.get('engine') not in {'speaches', 'api'}
            or not isinstance(inputs, dict) or set(inputs) != {'audio'}
            or not isinstance(inputs['audio'], str) or not re.fullmatch('[a-f0-9]{32}', inputs['audio'])
            or not isinstance(params, dict) or set(params) - {'language', 'prompt', 'temperature', 'response_format'}):
        raise ValueError('Transcription needs a Speaches/API engine and a local audio artifact')
    if ('language' in params and (not isinstance(params['language'], str)
            or not re.fullmatch('[a-z]{2,3}', params['language']))):
        raise ValueError('Use a supported ISO language code')
    if ('prompt' in params and (not isinstance(params['prompt'], str) or len(params['prompt']) > 8192)):
        raise ValueError('Transcription prompt is too long')
    temperature = params.get('temperature', 0)
    if (type(temperature) not in (int, float) or not math.isfinite(temperature)
            or not 0 <= temperature <= 1 or params.get('response_format', 'json') != 'json'):
        raise ValueError('Transcription accepts temperature 0–1 and JSON output')
    return {'path': '/audio/transcriptions', 'inputs': [inputs['audio']],
            'multipart': True, 'payload': {'model': body['model'], **params, 'response_format': 'json'}}


def prepare_speech(config, body):
    inputs, params = body['input'], body['parameters']
    if (config.get('engine') not in {'speaches', 'api'}
            or not isinstance(inputs, dict) or set(inputs) != {'text'}
            or not isinstance(inputs['text'], str) or not inputs['text'].strip()
            or len(inputs['text']) > 32768
            or not isinstance(params, dict) or set(params) - {'voice', 'speed', 'response_format'}):
        raise ValueError('Speech requires a Speaches/API engine, text and supported parameters')
    voice, speed, fmt = params.get('voice'), params.get('speed', 1.0), params.get('response_format', 'wav')
    if (not isinstance(voice, str) or not re.fullmatch('[A-Za-z0-9_.-]{1,128}', voice)
            or type(speed) not in (int, float) or not math.isfinite(speed) or not .25 <= speed <= 4
            or not isinstance(fmt, str) or fmt not in {'wav', 'mp3'}):
        raise ValueError('Speech needs an engine voice ID, speed 0.25–4 and WAV or MP3 output')
    return {'path': '/audio/speech', 'payload': {'model': body['model'], 'input': inputs['text'],
            'voice': voice, 'speed': speed, 'response_format': fmt}, 'inputs': [],
            'output': {'kind': 'audio', 'mime': 'audio/wav' if fmt == 'wav' else 'audio/mpeg',
                       'max_size': SPEECH_OUTPUT_LIMIT}}


def speech_result(response, plan, store, owner, cancelled):
    declared = response.headers.get('Content-Length')
    cap, fmt = plan['output']['max_size'], plan['payload']['response_format']
    if declared is not None and (not declared.isdecimal() or not 0 < int(declared) <= cap):
        raise ValueError('Invalid speech output length')
    mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
    allowed = {'wav': {'audio/wav', 'audio/x-wav', 'audio/wave'}, 'mp3': {'audio/mpeg', 'audio/mp3'}}
    if mime not in allowed[fmt]:
        raise ValueError('Unexpected speech output format')
    offset, prefix = 0, bytearray()
    while True:
        if cancelled():
            raise ConnectionAbortedError('Speech job cancelled')
        block = response.read1(64 * 1024)
        if not block:
            break
        if offset + len(block) > cap:
            raise ValueError('Speech output exceeds its reserved budget')
        if len(prefix) < 12:
            prefix.extend(block[:12 - len(prefix)])
        store.append(plan['output_id'], offset, block, owner=owner)
        offset += len(block)
    if not offset or (declared is not None and offset != int(declared)):
        raise ValueError('Speech output was empty or truncated')
    if (fmt == 'wav' and not (len(prefix) == 12 and prefix[:4] in (b'RIFF', b'RF64') and prefix[8:12] == b'WAVE')
            or fmt == 'mp3' and not (prefix[:3] == b'ID3' or len(prefix) >= 2 and prefix[0] == 255 and prefix[1] & 224 == 224)):
        raise ValueError('Speech output did not contain the requested audio format')
    if cancelled():
        raise ConnectionAbortedError('Speech job cancelled')
    output = store.seal(plan['output_id'], owner=owner, generated=True)
    return {'artifacts': [output], 'usage': {}}


def result(response, plan, *, store=None, owner='', cancelled=lambda: False):
    if response.status != 200:
        raise RuntimeError('upstream_http_' + str(response.status))
    if response.headers.get('Content-Encoding', 'identity') != 'identity':
        raise ValueError('Unsupported response encoding')
    if plan.get('output'):
        return speech_result(response, plan, store, owner, cancelled)
    body = bytearray()
    while True:
        chunk = response.read1(16384)
        if not chunk:
            break
        if len(body) + len(chunk) > 256 * 1024:
            raise ValueError('Structured inference response too large')
        body.extend(chunk)
    raw = json.loads(body)
    if plan.get('multipart'):
        # The result contains text only, never the original audio bytes.
        if not isinstance(raw, dict) or not isinstance(raw.get('text'), str) or len(raw['text']) > 65536:
            raise ValueError('Invalid or oversized transcription result')
        return {'text': raw['text'], 'usage': {}}
    rows = raw.get('results') if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or len(rows) != plan['payload']['top_n']:
        raise ValueError('Incomplete rerank result')
    seen, normalized = set(), []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError('Invalid rerank item')
        index, score = item.get('index'), item.get('relevance_score', item.get('score'))
        if (type(index) is not int or not 0 <= index < len(plan['payload']['documents']) or index in seen
                or type(score) not in (int, float) or not math.isfinite(score)):
            raise ValueError('Invalid rerank index or score')
        seen.add(index)
        value = {'index': index, 'relevance_score': score}
        if plan['return_documents']:
            value['document'] = plan['payload']['documents'][index]
        normalized.append(value)
    usage = raw.get('usage', {}) if isinstance(raw, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    return {'results': normalized, 'usage': {k: usage[k] for k in
            ('prompt_tokens', 'completion_tokens', 'total_tokens')
            if type(usage.get(k)) is int and 0 <= usage[k] <= 10**12}}
