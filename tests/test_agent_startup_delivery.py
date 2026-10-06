"""Full General Team startup through the actual Hub and platform reader.

Compilation/auth/database/serialization are real. This test stages or starts no
Apps; native execution is covered separately. Supply the matching Hub checkout.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from pantheon.apps.local_agent import compose_profile
from pantheon.platform.app_preset import fetch_hub_preset, startup_recipe
from pantheon.platform.local_profile import local_values
from test_local_agent_product import product
from test_general_agent_preset import complete_entries, compact_setup


@pytest.mark.asyncio
async def test_complete_general_team_hub_save_and_platform_fetch(complete_entries, tmp_path, monkeypatch):
    source = os.environ.get('AGENT_STARTUP_HUB_SOURCE')
    if not source:
        pytest.skip('Supply the matching Hub source for cross-repository startup acceptance')
    source = Path(source).resolve()
    monkeypatch.syspath_prepend(str(source))
    module_spec = importlib.util.spec_from_file_location('agent_startup_hub_fixture', source/'tests/test_app_startup.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    profile = compose_profile(complete_entries, compact_setup())
    owner = 'f_' + hashlib.sha256(b'alice').hexdigest()[:16]
    context = dict(controller='https://hub.test', trust_roots_pem='fixture-not-used-for-network',
        directory_root='/fixture/model-directory', workspace='/fixture/project',
        owner_credential={'ref': 'node-secret://control', 'endpoint': 'https://hub.test'},
        fleet_credential={'ref': 'node-secret://bus', 'endpoint': 'tls://broker.test:4222'})
    def app(name, value):
        ctx = {**context, 'fleet_event_prefix': f'fleet.{owner}.apps.{name}'}
        return dict(node_id='workspace', revision=profile['packages'][value['package']]['revision'],
            generation=0, scope=value['scope'], components=local_values(value['components'], ctx),
            bindings=local_values(value['bindings'], ctx))
    spec = startup_recipe(dict(owner=owner, operation_id='full-general-team', kind='model-services',
        apps={name: app(name, value) for name, value in profile['apps'].items()},
        model_apps={name: {**value, 'app': app(name, value['app'])} for name, value in profile['model_apps'].items()}))
    canonical = json.dumps(spec, separators=(',', ':'))
    assert len(canonical.encode()) > 64 * 1024  # Real schemas, without artificial padding.
    fixture = module.client.__wrapped__(tmp_path, monkeypatch)
    client, headers = await anext(fixture)
    try:
        path = '/api/fleet/apps/startup/default'
        response = await client.put(path, headers=headers(), json={'revision': 0, 'recipe': spec})
        assert response.status_code == 200, response.text
        assert response.json()['recipe'] == spec
        workload_headers = headers(scope='fleet')
        loaded = await fetch_hub_preset('https://hub.test'+path, hub='https://hub.test', owner=owner,
            token=workload_headers['Authorization'].removeprefix('Bearer '), transport=client._transport)
        assert loaded == spec
        assert set(loaded['apps']) == set(profile['apps'])
        assert loaded['apps']['agent']['components'] == spec['apps']['agent']['components']
        export = os.environ.get('AGENT_STARTUP_EXPORT')
        if export:
            output = Path(export)
            output.mkdir(mode=0o700, parents=True, exist_ok=True)
            (output/'general-team-startup.json').write_text(canonical)
    finally:
        # Resume the fixture normally so its post-yield database disposal runs.
        with pytest.raises(StopAsyncIteration):
            await anext(fixture)
