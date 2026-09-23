"""Typed inference job handles remain bound to their chosen Fleet deployment."""
import json
import re
from urllib.parse import urlsplit

from .media import MediaSession, artifact_ref, parse_artifact_ref, MAX_ARTIFACT


ACTIVE = {'queued', 'running', 'cancelling'}
STATES = ACTIVE | {'succeeded', 'failed', 'cancelled', 'unknown'}
PREFIX = '/inference/jobs'


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Invalid inference job identity')
    return value


def parse_job_ref(ref):
    parsed = urlsplit(ref)
    if (parsed.scheme != 'fleet-job' or parsed.query or parsed.fragment
            or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', parsed.netloc)
            or not parsed.path.startswith('/')):
        raise ValueError('Invalid Fleet inference job reference')
    return parsed.netloc, job_id(parsed.path[1:])


class InferenceSession:
    def __init__(self, client, row, grant, transport, *, model='', operation='', route=None):
        self.wire = MediaSession(client, row, grant, transport)
        self.model, self.operation = model, operation
        self.route = route or {}
        self.deployment = row['deployment_id']

    async def receipt(self, method, path, *, expected, status=200, **kwargs):
        _, raw = await self.wire.request(method, path, status=status, limit=256 * 1024, **kwargs)
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get('protocol') != 1 or record.get('job_id') != expected
                or record.get('state') not in STATES):
            raise ValueError('Invalid inference job receipt')
        if record.get('operation') in {'speech', 'image', 'video'} and record['state'] == 'succeeded':
            kind, mimes = {'image': ('image', {'image/png'}),
                           'video': ('video', {'video/mp4'}),
                           'speech': ('audio', {'audio/wav', 'audio/mpeg'})}[record['operation']]
            result = record.get('result')
            artifacts = result.get('artifacts') if isinstance(result, dict) else None
            if not isinstance(artifacts, list) or len(artifacts) != 1:
                raise ValueError('Inference job did not return its media artifact')
            row = artifacts[0]
            if (not isinstance(row, dict) or row.get('state') != 'ready' or row.get('kind') != kind
                    or not isinstance(row.get('id'), str) or not re.fullmatch('[a-f0-9]{32}', row['id'])
                    or row.get('purpose') != 'output' or row.get('mime') not in mimes
                    or type(row.get('size')) is not int or not 0 < row['size'] <= MAX_ARTIFACT
                    or row.get('received') != row['size']
                    or not isinstance(row.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
                raise ValueError('Invalid generated media receipt')
            # Bind an opaque ID to THIS deployment. Never forward upstream URLs.
            result['artifacts'] = [{**{k: row[k] for k in
                ('id', 'kind', 'mime', 'purpose', 'size', 'received', 'state', 'sha256')},
                'ref': artifact_ref(self.deployment, row.get('id'))}]
        record['ref'] = f'fleet-job://{self.deployment}/{expected}'
        return record

    async def submit(self, inputs, *, request_id, parameters=None):
        job_id(request_id)
        if not self.model or not self.operation:
            raise ValueError('Resolve a published model before submitting inference')
        if self.operation == 'transcription':
            if not isinstance(inputs, dict) or set(inputs) != {'audio'}:
                raise ValueError('Transcription requires an audio artifact reference')
            deployment, artifact = parse_artifact_ref(inputs['audio'])
            if deployment != self.deployment:
                raise ValueError('Audio belongs to another service; no transfer or inference was submitted')
            inputs = {'audio': artifact}
        body = {'job_id': request_id, 'model': self.model, 'operation': self.operation,
                'input': inputs, 'parameters': parameters or {}}
        encoded = json.dumps(body, allow_nan=False, separators=(',', ':')).encode()
        if len(encoded) > 128 * 1024:
            raise ValueError('Typed inference request exceeds 128 KiB; upload media separately')
        record = await self.receipt('POST', PREFIX, expected=request_id, status=202,
                                  content=encoded, headers={'Content-Type': 'application/json'})
        if record.get('model') != self.model or record.get('operation') != self.operation:
            raise ValueError('Inference receipt does not match the chosen model operation')
        return record

    async def status(self, request_id):
        return await self.receipt('GET', PREFIX + '/' + job_id(request_id), expected=request_id)

    async def list(self):
        _, raw = await self.wire.request('GET', PREFIX, limit=256 * 1024)
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get('protocol') != 1
                or not isinstance(value.get('jobs'), list) or len(value['jobs']) > 128):
            raise ValueError('Invalid inference job history')
        for row in value['jobs']:
            if not isinstance(row, dict) or row.get('state') not in STATES or 'result' in row:
                raise ValueError('Invalid inference job history record')
            row['ref'] = f'fleet-job://{self.deployment}/{job_id(row.get("job_id"))}'
        return value

    async def cancel(self, request_id):
        return await self.receipt('POST', PREFIX + '/' + job_id(request_id) + '/cancel', expected=request_id)

    async def reconcile(self, request_id):
        return await self.receipt('POST', PREFIX + '/' + job_id(request_id) + '/reconcile', expected=request_id)

    async def remove(self, request_id):
        _, raw = await self.wire.request('DELETE', PREFIX + '/' + job_id(request_id))
        if json.loads(raw) != {'removed': True}:
            raise ValueError('Inference job removal was not confirmed')
        return {'removed': True}
