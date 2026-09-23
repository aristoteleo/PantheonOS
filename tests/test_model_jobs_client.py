"""Bounded typed job receipts, fixed identities and no ambiguous mutation replay."""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from pantheon.models.jobs import InferenceSession, parse_job_ref


def session():
    value = object.__new__(InferenceSession)
    value.model, value.operation, value.deployment = 'ranker', 'rerank', 'mac'
    value.wire = AsyncMock()
    return value


@pytest.mark.parametrize('ref', ['fleet-job://other/job/extra', 'fleet-job://user@node/job',
    'fleet-job://mac/job?url=other', 'fleet-job://mac/../other', 'fleet-job://mac/%2F', 'https://mac/job'])
def test_job_reference_never_selects_urls_or_paths(ref):
    with pytest.raises(ValueError):
        parse_job_ref(ref)


@pytest.mark.asyncio
async def test_lost_submit_ack_does_not_replay_or_change_the_job():
    value = session()
    value.wire.request.side_effect = httpx.ReadError('ack lost')
    with pytest.raises(httpx.ReadError):
        await value.submit({'query': 'q', 'documents': ['a']}, request_id='original')
    assert value.wire.request.await_count == 1
    body = json.loads(value.wire.request.call_args.kwargs['content'])
    assert body['job_id'] == 'original'
    value.wire.request.side_effect = None
    value.wire.request.return_value = ({}, json.dumps({'protocol': 1, 'job_id': 'original', 'state': 'succeeded'}).encode())
    record = await value.status('original')
    assert record['ref'] == 'fleet-job://mac/original'
    assert value.wire.request.call_args.args == ('GET', '/inference/jobs/original')


@pytest.mark.asyncio
@pytest.mark.parametrize('jobs', [[{'job_id': 'job', 'state': 'succeeded', 'result': 'overshared'}],
    [{'job_id': '../path', 'state': 'running'}], [{'job_id': 'job', 'state': 'fake-success'}], [{}] * 129])
async def test_history_rejects_results_invalid_identities_and_unbounded_records(jobs):
    value = session()
    value.wire.request.return_value = ({}, json.dumps({'protocol': 1, 'jobs': jobs}).encode())
    with pytest.raises(ValueError):
        await value.list()
    assert value.wire.request.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('fields', [{'job_id': 'someone-else'}, {'state': 'ok'}, {'model': 'other'}])
async def test_submission_rejects_mismatched_receipts(fields):
    value = session()
    record = {'protocol': 1, 'job_id': 'original', 'state': 'running', 'model': 'ranker', 'operation': 'rerank', **fields}
    value.wire.request.return_value = ({}, json.dumps(record).encode())
    with pytest.raises(ValueError):
        await value.submit({'query': 'q', 'documents': ['a']}, request_id='original')
    assert value.wire.request.await_count == 1
