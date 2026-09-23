"""SGLang 0.5.20 video protocol primitives for durable job orchestration.

Creation and observation are deliberately separate. The caller must durably
record the returned ID before polling, and must never retry an uncertain POST.
DELETE is not cancellation in this engine and is intentionally not exposed.
No engine paths, returned URLs, prompts or provider errors enter receipts.
"""
import json
import math
import re
import struct
from urllib.parse import urlsplit


OUTPUT_LIMIT = 64 * 1024 * 1024
RECEIPT_LIMIT = 64 * 1024
STATES = {'queued', 'in_progress', 'completed', 'failed'}


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid upstream video identity')
    return value


def prepare(config, body):
    inputs, params = body.get('input'), body.get('parameters')
    if (config.get('engine') != 'sglang' or config.get('managed')
            or urlsplit(config.get('endpoint', '')).path.rstrip('/') != '/v1'
            or not isinstance(body.get('model'), str) or not 0 < len(body['model']) <= 200
            or not isinstance(inputs, dict) or set(inputs) != {'text'}
            or not isinstance(inputs['text'], str) or not inputs['text'].strip()
            or len(inputs['text']) > 32768 or not isinstance(params, dict)
            or set(params) - {'size', 'fps', 'num_frames', 'seed', 'num_inference_steps',
                             'guidance_scale', 'negative_prompt'}):
        raise ValueError('Video needs an attached SGLang Diffusion engine and supported parameters')
    size = params.get('size', '512x512')
    if not isinstance(size, str) or not re.fullmatch('[0-9]{2,4}x[0-9]{2,4}', size):
        raise ValueError('Use an explicit video size')
    width, height = map(int, size.split('x'))
    if any(v < 64 or v > 1920 or v % 8 for v in (width, height)) or width * height > 1920 * 1080:
        raise ValueError('Video dimensions exceed supported bounds')
    values = {'fps': params.get('fps', 16), 'num_frames': params.get('num_frames', 17)}
    for key, low, high in [('fps', 1, 60), ('num_frames', 1, 241),
                           ('seed', 0, 2**32-1), ('num_inference_steps', 1, 100)]:
        value = values.get(key, params.get(key))
        if value is not None and (type(value) is not int or not low <= value <= high):
            raise ValueError('Invalid video sampling parameter')
        if key in params and value is None:
            raise ValueError('Invalid video sampling parameter')
    if values['num_frames'] / values['fps'] > 30:
        raise ValueError('Video duration exceeds 30 seconds')
    scale = params.get('guidance_scale', 1)
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 <= scale <= 30:
        raise ValueError('Invalid video guidance scale')
    if 'negative_prompt' in params and (not isinstance(params['negative_prompt'], str)
            or len(params['negative_prompt']) > 32768):
        raise ValueError('Invalid video negative prompt')
    return {'driver': 'diffusion_video', 'path': '/videos', 'inputs': [],
            'payload': {'model': body['model'], 'prompt': inputs['text'], **params,
                        **values, 'size': size, 'n': 1},
            'output': {'kind': 'video', 'mime': 'video/mp4', 'max_size': OUTPUT_LIMIT}}


def receipt(response, *, expected_id=None):
    if response.status != 200:
        raise RuntimeError('upstream_http_' + str(response.status))
    if (response.headers.get('Content-Encoding', 'identity') != 'identity'
            or response.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
        raise ValueError('Expected a video job receipt')
    body = bytearray()
    while chunk := response.read1(16384):
        if len(body) + len(chunk) > RECEIPT_LIMIT:
            raise ValueError('Video receipt exceeds its budget')
        body.extend(chunk)
    raw = json.loads(body)
    if not isinstance(raw, dict):
        raise ValueError('Invalid video receipt')
    ident = job_id(raw.get('id'))
    state, progress = raw.get('status'), raw.get('progress', 0)
    if ((expected_id is not None and ident != expected_id) or state not in STATES
            or type(progress) not in (int, float) or not math.isfinite(progress)
            or not 0 <= progress <= 100):
        raise ValueError('Video observation changed identity or has invalid state')
    return {'id': ident, 'state': state, 'progress': progress}


def request(connector, call, *, plan=None, upstream_id=None):
    """One creation OR one observation; no retry, redirects or URL following."""
    if urlsplit(connector.config['endpoint']).path.rstrip('/') != '/v1':
        raise ValueError('SGLang Diffusion needs the /v1 endpoint')
    if (plan is None) == (upstream_id is None):
        raise ValueError('Choose creation or observation')
    path = '/videos' if plan is not None else '/videos/' + job_id(upstream_id)
    try:
        with connector.inference_request(path, plan['payload'] if plan is not None else None,
                call, method='POST' if plan is not None else 'GET') as response:
            with connector.lock:
                call['upstream'] = response
            return receipt(response, expected_id=upstream_id)
    finally:
        if connection := call.get('connection'):
            connection.close()


class MP4Boxes:
    """Incremental top-level container check; never buffer movie or frame data.

This checks framing, not codec validity. Actual model acceptance must also
decode the generated movie. Fragmented/open-ended boxes are not accepted yet.
"""
    def __init__(self, cap):
        self.cap, self.total, self.remaining = cap, 0, 0
        self.header, self.boxes, self.payloads = bytearray(), [], {}

    def feed(self, block):
        self.total += len(block)
        if self.total > self.cap:
            raise ValueError('Video exceeds reserved output budget')
        offset = 0
        while offset < len(block):
            if self.remaining:
                take = min(self.remaining, len(block) - offset)
                offset += take
                self.remaining -= take
                continue
            needed = 8 if len(self.header) < 8 else 16
            take = min(needed - len(self.header), len(block) - offset)
            self.header.extend(block[offset:offset+take])
            offset += take
            if len(self.header) < needed:
                continue
            size, kind = struct.unpack('>I4s', self.header[:8])
            if size == 1 and len(self.header) < 16:
                continue
            if size == 1:
                size = struct.unpack('>Q', self.header[8:16])[0]
            header_size = len(self.header)
            if (size < header_size or size > self.cap or len(self.boxes) >= 4096
                    or kind not in {b'ftyp', b'free', b'skip', b'wide', b'mdat', b'moov'}):
                raise ValueError('Unsupported or invalid MP4 box')
            if not self.boxes and (kind != b'ftyp' or not 16 <= size <= 4096):
                raise ValueError('Missing MP4 file type box')
            self.boxes.append(kind)
            self.payloads[kind] = self.payloads.get(kind, 0) + size - header_size
            self.remaining = size - header_size
            self.header.clear()

    def finish(self):
        if (self.header or self.remaining or self.boxes.count(b'ftyp') != 1
                or self.boxes.count(b'moov') != 1
                or not self.payloads.get(b'mdat') or not self.payloads.get(b'moov')):
            raise ValueError('Truncated or incomplete MP4 container')


def download(connector, call, upstream_id, plan, store, owner):
    ident = job_id(upstream_id)
    cap = plan['output']['max_size']
    try:
        with connector.inference_request('/videos/' + ident + '/content', None, call, method='GET') as response:
            with connector.lock:
                call['upstream'] = response
            if response.status != 200:
                raise RuntimeError('upstream_http_' + str(response.status))
            if (response.headers.get('Content-Encoding', 'identity') != 'identity'
                    or response.headers.get('Content-Type', '').split(';')[0].strip() != 'video/mp4'):
                raise ValueError('Expected MP4 video')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or not 0 < int(length) <= cap):
                raise ValueError('Invalid video length')
            framing, offset = MP4Boxes(cap), 0
            while True:
                if call['cancelled']:
                    raise ConnectionAbortedError('Video download cancelled')
                block = response.read1(64 * 1024)
                if not block:
                    break
                framing.feed(block)
                # A resumed GET may split chunks differently. Verify previously
                # committed bytes separately before appending the new suffix.
                with store.lock:
                    committed = store.row(plan['output_id'])['received']
                overlap = min(len(block), max(0, committed-offset))
                if overlap:
                    store.append(plan['output_id'], offset, block[:overlap], owner=owner)
                if overlap < len(block):
                    store.append(plan['output_id'], offset+overlap, block[overlap:], owner=owner)
                offset += len(block)
            framing.finish()
            if length is not None and offset != int(length):
                raise ValueError('Truncated video output')
            if call['cancelled']:
                raise ConnectionAbortedError('Video download cancelled')
            return {'artifacts': [store.seal(plan['output_id'], owner=owner, generated=True)], 'usage': {}}
    finally:
        if connection := call.get('connection'):
            connection.close()
