"""Project-scoped GUI dependency calls over real TLS, with no global resolver."""
import asyncio

import pytest

from pantheon.apps.dependency_client import DependencyCallError
from pantheon.chatroom.launch import ConfiguredAgentApplication
from test_agent_launch import prepared, snapshot
from test_agent_dependency_bindings import endpoint, forbid_ambient_tools, FUNCTION


def configured(root, endpoint):
    value = prepared(root, 'http://127.0.0.1:12345')
    spec = value['values']['agent']
    spec['projects'].append({'id': 'other', 'name': 'Other', 'path': str(root/'other')})
    spec['view_dependencies'] = {}
    for project, token in [('shared', 'a'*64), ('other', 'b'*64)]:
        value['credentials'][project] = {'endpoint': endpoint.url, 'key': token}
        spec['view_dependencies'][project] = {'toolsets': {'fixture_service': {
            'credential': project, 'functions': [FUNCTION], 'timeout_seconds': 2,
        }}}
    return value


@pytest.mark.asyncio
async def test_gui_calls_use_project_grants_without_constructing_agent_sessions(tmp_path, endpoint):
    app = ConfiguredAgentApplication('agent', data_dir=tmp_path/'data',
        configuration=snapshot(configured(tmp_path, endpoint)), dependency_ca_file=str(tmp_path/'cert.pem'))
    try:
        for path, expected in [('workspace', 'session-a'), ('other', 'session-b')]:
            result = await app.call_view_service(str(tmp_path/path), 'fixture_service', 'execute', {'command': 'pwd'})
            assert result == {'session': expected, 'command': 'pwd'}
        assert [call[1] for call in endpoint.calls] == ['a'*64, 'b'*64]
        assert all(call[2]['args'] == {'command': 'pwd'} for call in endpoint.calls)
        assert not app.chat_teams
        for path, service, method, args in [
            ('unattached', 'fixture_service', 'execute', {'command': 'pwd'}),
            ('workspace', 'shell', 'execute', {'command': 'pwd'}),
            ('workspace', 'fixture_service', 'delete_all', {}),
            ('workspace', 'fixture_service', 'execute', {'command': 'pwd', 'session_id': 'other'}),
            ('workspace', 'fixture_service', 'execute', {'command': 'pwd', '_node_id': 'other'}),
        ]:
            with pytest.raises(ValueError):
                await app.call_view_service(str(tmp_path/path), service, method, args)
        assert len(endpoint.calls) == 2
        endpoint.status = 403
        with pytest.raises(DependencyCallError):
            await app.call_view_service(str(tmp_path/'workspace'), 'fixture_service', 'execute', {'command': 'pwd'})
        assert len(endpoint.calls) == 3  # No retry or alternate/global route.
    finally:
        await app.cleanup()
    with pytest.raises(RuntimeError, match='closed'):
        await app.call_view_service(str(tmp_path/'workspace'), 'fixture_service', 'execute', {'command': 'pwd'})


@pytest.mark.asyncio
async def test_shutdown_drains_accepted_gui_dependency_call(tmp_path, endpoint):
    app = ConfiguredAgentApplication('agent', data_dir=tmp_path/'data',
        configuration=snapshot(configured(tmp_path, endpoint)), dependency_ca_file=str(tmp_path/'cert.pem'))
    endpoint.hold = True
    call = asyncio.create_task(app.call_view_service(str(tmp_path/'workspace'),
        'fixture_service', 'execute', {'command': 'write'}))
    assert await asyncio.to_thread(endpoint.entered.wait, 2)
    close = asyncio.create_task(app.cleanup())
    try:
        await asyncio.sleep(.05)
        assert not close.done()
        endpoint.release.set()
        assert (await call)['command'] == 'write'
        await close
    finally:
        endpoint.release.set()
        await asyncio.gather(call, close, return_exceptions=True)


@pytest.mark.parametrize('mutation', ['project', 'credential', 'groups', 'schema'])
def test_invalid_view_binding_is_rejected_before_data_creation(tmp_path, endpoint, mutation):
    value = configured(tmp_path, endpoint)
    view = value['values']['agent']['view_dependencies']
    if mutation == 'project':
        view['unknown'] = view.pop('shared')
    elif mutation == 'credential':
        view['shared']['toolsets']['fixture_service']['credential'] = 'absent'
    elif mutation == 'groups':
        view['shared']['unexpected'] = 'value'
    else:
        view['shared']['toolsets']['fixture_service']['functions'] = []
    with pytest.raises(ValueError, match='configuration'):
        ConfiguredAgentApplication('agent', data_dir=tmp_path/'data', configuration=snapshot(value))
    assert not (tmp_path/'data').exists()
