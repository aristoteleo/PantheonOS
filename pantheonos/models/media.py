"""Bounded binary artifacts on a frozen, authorized Fleet service connection.

Only small receipts belong in control messages. Uploads require a seekable binary
source; downloads write to a caller-owned staging stream. Publish that stream
only after download returns and its full checksum has been verified. No operation
automatically retries an uncertain submission or switches to another node.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit


CHUNK = 1024 * 1024
MAX_ARTIFACT = 512 * CHUNK
PREFIX = '/media/artifacts'


def artifact_ref(deployment, artifact):
    ref = f'fleet-artifact://{deployment}/{artifact}'
    parse_artifact_ref(ref)
    return ref


def parse_artifact_ref(ref):
    parts = urlsplit(ref)
    if (parts.scheme != 'fleet-artifact' or parts.query or parts.fragment
            or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', parts.netloc)
            or not re.fullmatch('/[a-f0-9]{32}', parts.path)):
        raise ValueError('Invalid Fleet media reference')
    return parts.netloc, parts.path[1:]


class MediaSession:
    def __init__(self, client, row, grant, transport):
        self.client = client
        self.deployment = row['deployment_id']
        self.origin = grant['origin']
        self.transport = transport
        self.headers = {'X-Model-Config': row['config_revision'], 'Accept-Encoding': 'identity'}
        if grant['access_token']:
            self.headers['Authorization'] = 'Bearer ' + grant['access_token']

    def path(self, ref):
        deployment, artifact = parse_artifact_ref(ref)
        if deployment != self.deployment:
            raise ValueError('Media belongs to another service; it was not transferred')
        return PREFIX + '/' + artifact

    async def request(self, method, path, *, status=200, limit=8192, headers=None, **kwargs):
        async with self.client.stream(method, self.origin + path,
                headers={**self.headers, **(headers or {})}, **kwargs) as response:
            if response.status_code != status:
                # Provider/node errors can contain credentials or local paths.
                raise RuntimeError(f'Fleet media request returned HTTP {response.status_code}')
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise ValueError('Compressed media responses are not supported')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise ValueError('Media response exceeds its size limit')
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > limit:
                    raise ValueError('Media response exceeds its size limit')
                body.extend(chunk)
            if length is not None and len(body) != int(length):
                raise ValueError('Media response was truncated')
            return response.headers, bytes(body)

    async def receipt(self, method, path, *, status=200, **kwargs):
        _, data = await self.request(method, path, status=status, **kwargs)
        row = json.loads(data)
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or not re.fullmatch('[a-f0-9]{32}', row['id'])
                or type(row.get('size')) is not int or not 0 < row['size'] <= MAX_ARTIFACT
                or type(row.get('received')) is not int or not 0 <= row['received'] <= row['size']
                or row.get('state') not in ('writing', 'ready', 'deleting')
                or not isinstance(row.get('sha256'), str)
                or (row['state'] == 'ready' and (row['received'] != row['size']
                    or not re.fullmatch('[a-f0-9]{64}', row['sha256'])))):
            raise ValueError('Invalid Fleet media receipt')
        if path != PREFIX and row['id'] != path.split('/')[3]:
            raise ValueError('Media response changed artifact identity')
        # Do not forward arbitrary fields such as provider-returned URLs/paths.
        return {**{k: row[k] for k in ('id', 'size', 'received', 'state', 'sha256')},
                **{k: row.get(k) for k in ('kind', 'mime', 'purpose', 'created')},
                'ref': artifact_ref(self.deployment, row['id'])}

    async def upload(self, source, *, request_key, kind, mime):
        """Resume the same declaration explicitly after a lost acknowledgement.

        Hash the source before declaring it. The node checks that a repeated key
        identifies exactly the same bytes and verifies the checksum before Ready.
        The caller must keep the source unchanged for the duration of this call.
        """
        if not isinstance(request_key, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', request_key):
            raise ValueError('Invalid media upload request key')
        source.seek(0, 2)
        size = source.tell()
        if not 0 < size <= MAX_ARTIFACT:
            raise ValueError('Media size must be between 1 byte and 512 MiB')
        source.seek(0)
        digest, count = hashlib.sha256(), 0
        while count < size:
            block = source.read(min(CHUNK, size - count))
            if not isinstance(block, bytes) or not block or len(block) > min(CHUNK, size - count):
                raise ValueError('Media source is not a stable binary stream')
            digest.update(block)
            count += len(block)
        expected_sha = digest.hexdigest()
        row = await self.receipt('POST', PREFIX, status=201,
            json=dict(request_key=request_key, kind=kind, mime=mime, size=size, sha256=expected_sha))
        ref, path = row['ref'], self.path(row['ref'])
        def check(current):
            if (current['ref'] != ref or current['size'] != size or current['kind'] != kind
                    or current['mime'] != mime or current['purpose'] != 'input'
                    or current['state'] not in ('writing', 'ready')):
                raise ValueError('Media upload receipt changed declaration')
        check(row)
        while row['state'] == 'writing' and row['received'] < size:
            offset = row['received']
            source.seek(offset)
            block = source.read(min(CHUNK, size - offset))
            if not isinstance(block, bytes) or len(block) != min(CHUNK, size - offset):
                raise ValueError('Media source changed during upload')
            row = await self.receipt('PUT', path, headers={'Upload-Offset': str(offset),
                                     'Content-Type': 'application/octet-stream'}, content=block)
            check(row)
            if row['state'] != 'writing' or row['received'] != offset + len(block):
                raise ValueError('Media upload offset changed unexpectedly')
        if row['state'] == 'writing':
            row = await self.receipt('POST', path + '/complete')
            check(row)
        if row['state'] != 'ready' or row['sha256'] != expected_sha:
            raise ValueError('Media upload checksum was not confirmed')
        return row

    async def metadata(self, ref):
        return await self.receipt('GET', self.path(ref))

    async def download(self, ref, destination):
        """Write bounded chunks; caller publishes the staged file only on success."""
        path = self.path(ref)
        row = await self.metadata(ref)
        if row['state'] != 'ready':
            raise ValueError('Media is not ready')
        digest = hashlib.sha256()
        offset = 0
        while offset < row['size']:
            end = min(offset + CHUNK, row['size']) - 1
            headers, data = await self.request('GET', path + '/content', status=206,
                limit=end - offset + 1, headers={'Range': f'bytes={offset}-{end}'})
            if (headers.get('Content-Range') != f"bytes {offset}-{end}/{row['size']}"
                    or headers.get('ETag') != '"' + row['sha256'] + '"'
                    or len(data) != end - offset + 1):
                raise ValueError('Media download range or identity changed')
            if destination.write(data) != len(data):
                raise OSError('Media destination did not accept all bytes')
            digest.update(data)
            offset += len(data)
        if digest.hexdigest() != row['sha256']:
            raise ValueError('Media download checksum mismatch')
        return row

    async def remove(self, ref):
        _, data = await self.request('DELETE', self.path(ref))
        if json.loads(data) != {'removed': True}:
            raise ValueError('Media removal was not confirmed')
