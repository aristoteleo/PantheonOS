"""SGLang 0.5.20 image artifacts: bounded receipts and same-endpoint binary reads.

Upstream paths, credentials and base64 never become client-visible artifacts.
Only the exact local image content route is accepted, without redirects. Engine
outputs remain engine-owned; removing a Fleet job removes only its retained copy.
"""
import json
import re
import struct
import zlib
from urllib.parse import urlsplit


def checked(response):
    if response.status != 200:
        raise RuntimeError('upstream_http_' + str(response.status))
    if response.headers.get('Content-Encoding', 'identity') != 'identity':
        raise ValueError('Unsupported image response encoding')


def receipt(response, call):
    checked(response)
    if response.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
        raise ValueError('Expected image receipt')
    body = bytearray()
    while True:
        if call['cancelled']:
            raise ConnectionAbortedError('Image job cancelled')
        chunk = response.read1(16384)
        if not chunk:
            break
        if len(body) + len(chunk) > 64 * 1024:
            raise ValueError('Image receipt too large')
        body.extend(chunk)
    value = json.loads(body)
    rows = value.get('data') if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('Expected one generated image')
    path = rows[0].get('url')
    # The selected engine origin/API prefix is configuration, never inferred
    # from a returned URL. Reject cloud URLs, traversal, queries and encodings.
    if not isinstance(path, str) or not re.fullmatch(r'/v1/images/[A-Za-z0-9_-]{1,128}/content', path):
        raise ValueError('Image must remain on the selected engine')
    return path.removeprefix('/v1')


def image(connector, plan, call, store, owner):
    if urlsplit(connector.config['endpoint']).path.rstrip('/') != '/v1':
        raise ValueError('SGLang Diffusion needs the /v1 endpoint')
    response = connector.inference_request(plan['path'], plan['payload'], call)
    with response:
        with connector.lock:
            call['upstream'] = response
        path = receipt(response, call)
    call['connection'].close()
    if call['cancelled']:
        raise ConnectionAbortedError('Image job cancelled')
    response = connector.inference_request(path, None, call, method='GET')
    with response:
        with connector.lock:
            call['upstream'] = response
        checked(response)
        cap = plan['output']['max_size']
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdecimal() or not 0 < int(length) <= cap):
            raise ValueError('Invalid image length')
        if response.headers.get('Content-Type', '').split(';')[0].strip() != 'image/png':
            raise ValueError('Expected PNG image')
        offset, header, tail = 0, bytearray(), b''
        while True:
            if call['cancelled']:
                raise ConnectionAbortedError('Image job cancelled')
            block = response.read1(64 * 1024)
            if not block:
                break
            if offset + len(block) > cap:
                raise ValueError('Image exceeds reserved output budget')
            header.extend(block[:max(0, 33-len(header))])
            tail = (tail + block)[-12:]
            store.append(plan['output_id'], offset, block, owner=owner)
            offset += len(block)
        if offset < 45 or length is not None and int(length) != offset:
            raise ValueError('Image output was empty or truncated')
        if header[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
            raise ValueError('Invalid PNG header')
        if (struct.unpack('>I', header[29:33])[0] != zlib.crc32(header[12:29])
                or tail != b'\x00\x00\x00\x00IEND\xaeB`\x82'):
            raise ValueError('PNG header checksum or end marker is invalid')
        width, height = struct.unpack('>II', header[16:24])
        if not 0 < width <= 2048 or not 0 < height <= 2048:
            raise ValueError('Image dimensions exceed the supported bounds')
        if call['cancelled']:
            raise ConnectionAbortedError('Image job cancelled')
        output = store.seal(plan['output_id'], owner=owner, generated=True)
        return {'artifacts': [output], 'usage': {}}
