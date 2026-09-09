"""Replay the argument envelopes from chat 9447d3ef without losing payloads."""
from unittest.mock import AsyncMock
import pytest
from apps.desktop.toolset import DesktopToolSet

@pytest.fixture
def desktop(monkeypatch):
    tool = DesktopToolSet()
    monkeypatch.setattr(tool, '_desktop_window', lambda _: {'app_id': 'pkg:integrated-notebook'})
    monkeypatch.setattr(tool, '_desktop_request', AsyncMock(return_value={'success': True}))
    return tool

@pytest.mark.asyncio
@pytest.mark.parametrize('envelope', [
    {'kwargs': {'cell_type': 'code', 'source': 'value = 41\nvalue + 1'}},
    {'args': {}, 'kwargs': {'cell_type': 'code', 'source': 'value = 41\nvalue + 1'}},
    {'args': {'cell_type': 'code'}, 'kwargs': {'source': 'value = 41\nvalue + 1'}},
])
async def test_legacy_kwargs_never_silently_creates_an_empty_cell(desktop, envelope):
    assert (await desktop.desktop_call('win-130', 'add_cell', **envelope))['success']
    assert desktop._desktop_request.call_args.args[1]['args'] == {
        'cell_type': 'code', 'source': 'value = 41\nvalue + 1'}

@pytest.mark.asyncio
@pytest.mark.parametrize('envelope', [
    {'kwargs': {'cell_id': 'cell'}}, {'args': {'cell_id': 'cell'}, 'kwargs': None},
    {'_args': '{"cell_id":"cell"}'},
])
async def test_execute_keeps_cell_identity(desktop, envelope):
    await desktop.desktop_call('win-130', _action='execute_cell', **envelope)
    assert desktop._desktop_request.call_args.args[1] == {
        'window_id': 'win-130', 'action': 'execute_cell', 'args': {'cell_id': 'cell'}}

@pytest.mark.asyncio
@pytest.mark.parametrize('envelope', [
    {'args': {'content': 'one'}, 'kwargs': {'content': 'two'}},
    {'kwargs': ['not an object']}, {'args': '{bad'},
])
async def test_invalid_or_ambiguous_payload_has_no_side_effect(desktop, envelope):
    assert not (await desktop.desktop_call('win-130', 'add_cell', **envelope))['success']
    desktop._desktop_request.assert_not_called()
