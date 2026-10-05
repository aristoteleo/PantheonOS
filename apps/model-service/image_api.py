"""OpenAI-compatible Images API over existing typed jobs and media artifacts.

The node owns credentials and the selected endpoint. Reference images are sealed
local artifacts, streamed as multipart parts in caller order. Provider base64 is
bounded and retained as binary; returned URLs are never followed. No SDK needed.
"""
import base64
import binascii
import hashlib
import json
import re
import struct
import uuid
import zlib

MIMES = {'png': 'image/png', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}
MAX_INPUT = 50 * 1024 * 1024
MAX_INPUTS = 128 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024


def prepare(body):
    if (not isinstance(body, dict)
            or set(body) != {'job_id', 'model', 'operation', 'input', 'parameters'}
            or body['operation'] != 'image' or not isinstance(body['model'], str)
            or not 0 < len(body['model']) <= 200):
        raise ValueError('Invalid image request')
    inputs, params = body['input'], body['parameters']
    if (not isinstance(inputs, dict) or set(inputs) - {'text', 'images'}
            or not isinstance(inputs.get('text'), str) or not inputs['text'].strip()
            or len(inputs['text']) > 32768 or not isinstance(params, dict)
            or set(params) - {'size', 'quality', 'background', 'output_format', 'response_format', 'n'}):
        raise ValueError('Image generation needs text, optional image artifacts and supported parameters')
    images = inputs.get('images', [])
    if (not isinstance(images, list) or len(images) > 16
            or 'images' in inputs and not images
            or any(not isinstance(i, str) or not re.fullmatch('[a-f0-9]{32}', i) for i in images)):
        raise ValueError('Image editing needs one to sixteen local artifact identities')
    size = params.get('size', 'auto')
    if size != 'auto':
        if not isinstance(size, str) or not re.fullmatch('[0-9]{2,4}x[0-9]{2,4}', size):
            raise ValueError('Use auto or explicit image dimensions')
        if any(not 64 <= v <= 4096 for v in map(int, size.split('x'))):
            raise ValueError('Image dimensions must be between 64 and 4096')
    for key, allowed in (
        ('quality', {'auto', 'low', 'medium', 'high', 'xhigh', 'max', 'standard', 'hd'}),
        ('background', {'auto', 'transparent', 'opaque'}),
        ('output_format', set(MIMES)), ('response_format', {'b64_json'}),
    ):
        if key in params and (not isinstance(params[key], str) or params[key] not in allowed):
            raise ValueError('Unsupported image option: ' + key)
    if type(params.get('n', 1)) is not int or params.get('n', 1) != 1:
        raise ValueError('One image is generated per durable job')
    fmt = params.get('output_format', 'png')
    if params.get('background') == 'transparent' and fmt == 'jpeg':
        raise ValueError('Transparent images require PNG or WebP')
    # GPT Image returns base64 without response_format. Older compatible engines
    # can explicitly select b64_json; never infer protocol support from a name.
    return {'driver': 'api_image', 'inputs': list(dict.fromkeys(images)), 'image_order': images,
            'path': '/images/edits' if images else '/images/generations',
            'payload': {'model': body['model'], 'prompt': inputs['text'], **params, 'n': 1},
            'output': {'kind': 'image', 'mime': MIMES[fmt], 'max_size': MAX_OUTPUT}}


def validate_inputs(plan, store):
    """Admission transaction pins immutable inputs before any upstream call."""
    images, total = [], 0
    for artifact in plan['image_order']:
        row = store.row(artifact)
        if (row['state'] != 'ready' or row['kind'] != 'image' or row['mime'] not in MIMES.values()
                or not 0 < row['size'] < MAX_INPUT):
            raise ValueError('Reference images must be sealed PNG, JPEG or WebP artifacts under 50 MiB')
        total += row['size']
        if total > MAX_INPUTS:
            raise ValueError('Reference images exceed the combined 128 MiB budget')
        images.append(store.metadata(row))
    plan['images'] = images


def edit_request(connector, plan, call, store):
    boundary = 'fleet-' + uuid.uuid4().hex
    fields = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                      f'{value}\r\n').encode() for name, value in plan['payload'].items())
    headers = [(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; '
                f'filename="reference-{index}.{image["mime"].split("/")[1]}"\r\n'
                f'Content-Type: {image["mime"]}\r\n\r\n').encode()
               for index, image in enumerate(plan['images'])]
    end = f'--{boundary}--\r\n'.encode()
    length = len(fields) + len(end) + sum(len(h) + a['size'] + 2 for h, a in zip(headers, plan['images']))

    def chunks():
        yield fields
        for header, image in zip(headers, plan['images']):
            yield header
            offset, digest = 0, hashlib.sha256()
            while offset < image['size']:
                if call['cancelled']:
                    raise ConnectionAbortedError('Image submission cancelled')
                receipt, data = store.read(image['id'], offset, min(64 * 1024, image['size'] - offset))
                if not data or receipt != image:
                    raise ValueError('Reference image changed during submission')
                offset += len(data)
                digest.update(data)
                yield data
            if digest.hexdigest() != image['sha256']:
                raise ValueError('Reference image checksum changed during submission')
            yield b'\r\n'
        yield end

    return connector.inference_request(plan['path'], None, call, body=chunks(),
        content_type='multipart/form-data; boundary=' + boundary, content_length=length)


def validate_envelope(data, mime):
    """Check container identity/completeness; decoding remains the viewer's job."""
    if mime == 'image/png':
        if len(data) < 45 or data[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
            raise ValueError('Invalid PNG image')
        width, height = struct.unpack('>II', data[16:24])
        if not 0 < width <= 4096 or not 0 < height <= 4096:
            raise ValueError('PNG dimensions exceed supported bounds')
        offset, has_pixels = 8, False
        while offset + 12 <= len(data):
            size = struct.unpack('>I', data[offset:offset + 4])[0]
            end = offset + 12 + size
            if end > len(data) or zlib.crc32(memoryview(data)[offset + 4:end - 4]) != struct.unpack('>I', data[end - 4:end])[0]:
                raise ValueError('Invalid PNG chunk')
            kind = data[offset + 4:offset + 8]
            has_pixels |= kind == b'IDAT'
            if kind == b'IEND':
                if size or end != len(data) or not has_pixels:
                    raise ValueError('Incomplete PNG image')
                return
            offset = end
        raise ValueError('PNG end marker missing')
    if mime == 'image/jpeg':
        if len(data) < 4 or data[:3] != b'\xff\xd8\xff' or data[-2:] != b'\xff\xd9':
            raise ValueError('Invalid JPEG image')
    elif mime == 'image/webp':
        if (len(data) < 20 or data[:4] != b'RIFF' or data[8:12] != b'WEBP'
                or data[12:16] not in (b'VP8 ', b'VP8L', b'VP8X')
                or struct.unpack('<I', data[4:8])[0] + 8 != len(data)):
            raise ValueError('Invalid WebP image')
    else:
        raise ValueError('Unsupported image MIME type')


def image(connector, plan, call, store, owner):
    response = (edit_request(connector, plan, call, store) if plan['images']
                else connector.inference_request(plan['path'], plan['payload'], call))
    with response:
        with connector.lock:
            call['upstream'] = response
        if response.status != 200:
            raise RuntimeError('upstream_http_' + str(response.status))
        if (response.headers.get('Content-Encoding', 'identity') != 'identity'
                or response.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
            raise ValueError('Expected an uncompressed image JSON response')
        cap = 4 * ((plan['output']['max_size'] + 2) // 3) + 64 * 1024
        declared = response.headers.get('Content-Length')
        if declared is not None and (not declared.isdecimal() or not 0 < int(declared) <= cap):
            raise ValueError('Image response exceeds the reserved budget')
        raw = bytearray()
        while True:
            if call['cancelled']:
                raise ConnectionAbortedError('Image job cancelled')
            block = response.read1(64 * 1024)
            if not block:
                break
            if len(raw) + len(block) > cap:
                raise ValueError('Image response exceeds the reserved budget')
            raw.extend(block)
        if declared is not None and len(raw) != int(declared):
            raise ValueError('Truncated image response')
        value = json.loads(raw)
        del raw
        rows = value.get('data') if isinstance(value, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError('Expected one generated image')
        encoded = rows[0].get('b64_json')
        if not isinstance(encoded, str) or len(encoded) > cap - 64 * 1024:
            raise ValueError('Expected bounded base64 image data; remote URLs are not accepted')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('Invalid image base64') from exc
        if not 0 < len(data) <= plan['output']['max_size']:
            raise ValueError('Image exceeds reserved output budget')
        validate_envelope(data, plan['output']['mime'])
        for offset in range(0, len(data), 64 * 1024):
            if call['cancelled']:
                raise ConnectionAbortedError('Image job cancelled')
            store.append(plan['output_id'], offset, data[offset:offset + 64 * 1024], owner=owner)
        if call['cancelled']:
            raise ConnectionAbortedError('Image job cancelled')
        output = store.seal(plan['output_id'], owner=owner, generated=True)
        usage = value.get('usage', {})
        if not isinstance(usage, dict):
            usage = {}
        return {'artifacts': [output], 'usage': {key: usage[key] for key in
            ('input_tokens', 'output_tokens', 'total_tokens') if type(usage.get(key)) is int and 0 <= usage[key] <= 10**12}}
