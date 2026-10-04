"""Private settings RPC over the ordinary host, including a process restart."""
import asyncio
import json

import pytest

from test_agent_model_scope import endpoint as model_endpoint
from test_agent_native_process import native_process, request


async def ready(child, base, root):
    for _ in range(200):
        try:
            await request(base, '/health')
            return
        except OSError:
            assert child.poll() is None, (root/'process.log').read_text()[-12000:]
            await asyncio.sleep(.05)
    pytest.fail('Native Agent did not become ready')


@pytest.mark.asyncio
async def test_settings_rpc_persists_without_live_reload_and_applies_after_restart(tmp_path, model_endpoint):
    async def call(base, method, **args):
        response = await request(base, '/rpc', {'method': method, 'args': args})
        assert response['success']
        return response['result']

    with native_process(tmp_path, model_endpoint.url) as (child, base):
        await ready(child, base, tmp_path)
        original = await call(base, 'get_agent_settings')
        assert original['effective']['llm_retry']['max_retries'] == 3
        saved = await call(base, 'save_agent_settings', expected_revision=original['revision'],
                           overrides={'llm_retry': {'max_retries': 1}})
        assert saved['success'] and saved['restart_required']
        assert saved['effective']['llm_retry']['max_retries'] == 3
        assert 'process-fixture' not in json.dumps(saved)
        conflict = await call(base, 'save_agent_settings', expected_revision=original['revision'], overrides={})
        assert not conflict['success'] and conflict['conflict']
    with native_process(tmp_path, model_endpoint.url) as (child, base):
        await ready(child, base, tmp_path)
        loaded = await call(base, 'get_agent_settings')
        assert loaded['revision'] == saved['revision']
        assert loaded['active_revision'] == saved['revision']
        assert not loaded['restart_required']
        assert loaded['effective']['llm_retry']['max_retries'] == 1
    assert not (tmp_path/'workspace'/'.pantheon'/'settings.json').exists()
