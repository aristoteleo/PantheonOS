"""Replay the argument envelopes from chat 9447d3ef without losing payloads."""
from unittest.mock import AsyncMock
from types import SimpleNamespace
import pytest
from apps.desktop.toolset import DesktopToolSet
from pantheon.providers import ToolSetProvider

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

@pytest.mark.asyncio
async def test_desktop_call_survives_remote_discovery_and_dispatches(desktop):
    async def invoke(name, args):
        return await getattr(desktop, name)(**args)
    proxy = SimpleNamespace(
        toolset_name='desktop', list_tools=desktop.list_tools,
        invoke=AsyncMock(side_effect=invoke),
    )
    provider = ToolSetProvider(proxy)
    tools = {tool.name: tool for tool in await provider.list_tools()}
    assert 'desktop_call' in tools
    schema = tools['desktop_call'].inputSchema['parameters']
    assert {'window_id', 'action', 'args', 'kwargs', '_action', '_args'} <= set(schema['properties'])
    assert schema['required'] == ['window_id']
    for envelope in (
        {'action': 'loadDataset', 'args': {'id': 'e11_5_embryo'}},
        {'_action': 'loadDataset', '_args': {'id': 'e11_5_embryo'}},
    ):
        result = await provider.call_tool('desktop_call', {'window_id': 'win-138', **envelope})
        assert result['success']
        assert desktop._desktop_request.call_args.args[1] == {
            'window_id': 'win-138', 'action': 'loadDataset', 'args': {'id': 'e11_5_embryo'},
        }

def test_underscore_alias_does_not_collide_with_an_existing_parameter():
    from pantheon.funcdesc.desc import Description, Value
    from pantheon.funcdesc.pydantic import desc_to_pydantic
    model = desc_to_pydantic(Description(name='collision', inputs=[
        Value(str, name='_action'), Value(int, name='tool_param_action'),
    ]))['inputs']
    payload = {'_action': 'loadDataset', 'tool_param_action': 7}
    assert set(model.model_json_schema()['properties']) == set(payload)
    assert model.model_validate(payload).model_dump(by_alias=True) == payload
