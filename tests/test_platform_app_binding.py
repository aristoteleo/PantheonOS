"""App service binding is independent of chat/session state and node cwd."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.platform.service import PlatformService
from pantheon.platform.apps_api import resolve_app_service


@pytest.fixture
def resolver(monkeypatch):
    from pantheon.apps import resolver as module
    resolver = SimpleNamespace(resolves=lambda name: name in ('file_manager', 'file_transfer', 'shell', 'pty'),
        project_scope=lambda path: 'project:' + path,
        ensure_instance=AsyncMock(return_value='file-service'))
    monkeypatch.setattr(module, 'get_shared_resolver', lambda: resolver)
    return resolver


def test_binding_uses_deployment_workspace_not_process_cwd(resolver, tmp_path, monkeypatch):
    service = PlatformService(workspace_path=str(tmp_path / 'home'))
    monkeypatch.chdir(tmp_path)
    result = asyncio.run(service.resolve_app_service('file_transfer'))
    assert result['success'] and result['invocation'] == 'direct'
    assert result['workdir'] == str(tmp_path / 'home')
    resolver.ensure_instance.assert_awaited_once_with('file_transfer',
        scope='project:' + str(tmp_path / 'home'), workdir=str(tmp_path / 'home'))


def test_binding_explicit_workspace_and_service(resolver):
    result = asyncio.run(PlatformService().resolve_app_service('shell', '/work/a'))
    assert result['service_name'] == 'shell'
    resolver.ensure_instance.assert_awaited_once_with('shell', scope='project:/work/a', workdir='/work/a')


def test_node_transfer_uses_files_envelope_and_existing_authorization(resolver):
    result = asyncio.run(PlatformService().resolve_app_service('file_transfer', '/wrong/cloud/path', 'mac'))
    assert result['node_id'] == 'mac'
    assert result['workdir'] is None
    assert result['invocation'] == 'file_transfer'
    resolver.ensure_instance.assert_awaited_once_with('file_manager', node_id='mac')
    resolver.ensure_instance.reset_mock()
    resolver.ensure_instance.side_effect = ValueError('Node is not a member of this user’s fleet')
    result = asyncio.run(PlatformService().resolve_app_service('file_manager', node_id='foreign'))
    assert result['success'] is False
    assert 'not a member' in result['error']
    resolver.ensure_instance.assert_awaited_once_with('file_manager', node_id='foreign')


@pytest.mark.parametrize('name,workdir,node', [('missing', None, None), ('shell', None, 'mac'),
    ('file_transfer', None, 123)])
def test_invalid_bindings_never_start_a_service(resolver, name, workdir, node):
    result = asyncio.run(PlatformService().resolve_app_service(name, workdir, node))
    assert result['success'] is False
    resolver.ensure_instance.assert_not_awaited()


def test_legacy_unscoped_resolution_remains_unscoped(resolver):
    result = asyncio.run(resolve_app_service('file_manager'))
    assert result['success']
    resolver.ensure_instance.assert_awaited_once_with('file_manager')
