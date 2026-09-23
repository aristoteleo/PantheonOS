"""Explicit typed adapters; caller input can never choose an endpoint or path.

SGLang's /v1/rerank is not an official OpenAI endpoint. Its list response and
common API-connector results/relevance_score responses are normalized below.
Other modalities require their own validated adapters, not chat-completion casts.
"""
import json
import math


def prepare(config, body):
    if (not isinstance(body, dict) or set(body) != {'job_id', 'model', 'operation', 'input', 'parameters'}
            or not isinstance(body['model'], str) or not 0 < len(body['model']) <= 200
            or body['operation'] != 'rerank' or config.get('engine') not in {'sglang', 'api'}):
        raise ValueError('This typed operation is not supported by the configured engine adapter')
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


def result(response, plan):
    if response.status != 200:
        raise RuntimeError('upstream_http_' + str(response.status))
    if response.headers.get('Content-Encoding', 'identity') != 'identity':
        raise ValueError('Unsupported response encoding')
    body = bytearray()
    while True:
        chunk = response.read1(16384)
        if not chunk:
            break
        if len(body) + len(chunk) > 256 * 1024:
            raise ValueError('Rerank response too large')
        body.extend(chunk)
    raw = json.loads(body)
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
