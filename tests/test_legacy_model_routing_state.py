"""Legacy browser migration may observe routing, never credentials."""
import json
import pytest
from pantheon.chatroom.room import ChatRoom
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.platform.service import PlatformService


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,base,key,expected', [
    ('true', 'https://private-endpoint.test', 'private-key', (True, True)),
    ('yes', '', 'private-key', (True, False)),
    ('1', 'https://private-endpoint.test', '', (True, False)),
    ('false', 'https://private-endpoint.test', 'private-key', (False, False)),
    ('', '', '', (False, False)),
])
async def test_legacy_budget_observation_is_read_only_and_secret_free(monkeypatch, enabled, base, key, expected):
    monkeypatch.setenv('LLM_FORCE_PROXY', enabled)
    monkeypatch.setenv('PANTHEON_PLATFORM_PROXY_BASE', base)
    monkeypatch.setenv('PANTHEON_PLATFORM_PROXY_KEY', key)
    room = object.__new__(ChatRoom)
    result = await room.get_llm_proxy_state()
    assert result == dict(protocol=1, enabled=expected[0], configured=expected[1])
    assert 'private' not in json.dumps(result)
    import os
    assert os.environ['LLM_FORCE_PROXY'] == enabled
    assert os.environ['PANTHEON_PLATFORM_PROXY_BASE'] == base
    assert os.environ['PANTHEON_PLATFORM_PROXY_KEY'] == key


def test_process_global_routing_observation_is_only_on_legacy_facade():
    assert ChatRoom.get_llm_proxy_state._is_tool
    assert not hasattr(AgentRuntime, 'get_llm_proxy_state')
    assert not hasattr(PlatformService, 'get_llm_proxy_state')
