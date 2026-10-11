"""An allocator resolves late provider references from the deployment status."""
import json
from types import SimpleNamespace

import httpx
import pytest

from pantheon.apps.late_refs import DeploymentInstances, has_late

R = 'a' * 64


def instances(apps, calls):
    def handler(request):
        calls.append(request.url.path)
        assert request.headers['Authorization'] == 'Bearer owner-key'
        return httpx.Response(200, json={'status': {'apps': apps}})
    return DeploymentInstances(SimpleNamespace(endpoint='https://controller.test', key='owner-key'),
                               transport=httpx.MockTransport(handler), ttl=60)


@pytest.mark.asyncio
async def test_late_references_resolve_to_running_instances():
    calls = []
    late = {'consumer': {'$late_app': 'agent', 'deployment': 'general-team'},
            'files': {'provider': {'$late_app': 'files', 'deployment': 'general-team', 'component': 'backend', 'port': 'http'}}}
    assert has_late(late) and not has_late({'x': {'$app': 'y'}})
    source = instances({'agent': {'state': 'ready', 'node_id': 'brain', 'instance_id': 'i1', 'revision': R, 'generation': 4},
                        'files': {'state': 'starting', 'node_id': 'ws', 'instance_id': 'i2', 'revision': R, 'generation': 2}}, calls)
    resolved = await source.resolve(late)
    assert resolved['consumer'] == {'node_id': 'brain', 'instance_id': 'i1', 'revision': R, 'generation': 4}
    assert resolved['files']['provider'] == {'node_id': 'ws', 'instance_id': 'i2', 'revision': R, 'generation': 2,
                                             'component': 'backend', 'port': 'http'}
    await source.resolve(late)
    assert calls == ['/deployments/general-team']  # cached within its lifetime
    await source.aclose()


@pytest.mark.asyncio
async def test_a_provider_that_is_not_running_is_refused():
    source = instances({'files': {'state': 'waiting'}}, [])
    with pytest.raises(ValueError, match='files is not running'):
        await source.resolve({'$late_app': 'files', 'deployment': 'general-team'})
    await source.aclose()
