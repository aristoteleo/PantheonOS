"""Owner migration reservation precedes source fencing and survives failures."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.data_fence import CONTROL_FILES
from pantheon.chatroom.data_transition import import_reservation, transition_state
from pantheon.chatroom.migration_import import reserve_import, import_backup
from pantheon.chatroom.migration_profile import LocalAgentMigration
from test_agent_migration import legacy
from test_agent_migration_import import prepared
from test_agent_migration_backup import user_tree
from test_agent_migration_credentials import vault, stage, read_key
from test_agent_model_scope import endpoint


def reserve(spec, target, **kwargs):
    return reserve_import(spec, target=target, operation='move', namespace='migrated-agent',
                          request_digest=kwargs.pop('request_digest', 'a'*64), **kwargs)


def launch(spec, target):
    return AgentAppData(target, namespace='migrated-agent', projects=AppProjects(spec['projects']))


def test_reservation_blocks_empty_start_before_any_source_fence(legacy, tmp_path):
    target = tmp_path/'agent'
    original = user_tree(tmp_path)
    reservation = reserve(legacy, target)
    assert reservation == import_reservation(target)
    assert not any(p.name in CONTROL_FILES for p in Path(legacy['project_config']).rglob('*'))
    assert transition_state(target) is None
    with pytest.raises(ValueError, match='reserved for migration'):
        launch(legacy, target)
    assert not (target/'instances').exists() and not (target/'agent-data-format.json').exists()
    assert {p: raw for p, raw in user_tree(tmp_path).items() if not p.startswith('agent/')} == original
    assert reserve(legacy, target) == reservation
    with pytest.raises(ValueError, match='another migration request'):
        reserve(legacy, target, request_digest='b'*64)


def test_matching_import_opens_reserved_admission_and_retries(prepared):
    spec, fence, backup, target = prepared
    reserve(spec, target)
    receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
    assert reserve(spec, target) == import_reservation(target)
    app = launch(spec, target); app.close()
    assert import_backup(backup['directory'], digest=backup['sha256'], fence=fence) == receipt


def test_reservation_cannot_adopt_initialized_data(legacy, tmp_path):
    target = tmp_path/'agent'
    app = launch(legacy, target); app.close()
    before = user_tree(target)
    with pytest.raises(ValueError, match='before initializing'):
        reserve(legacy, target)
    assert user_tree(target) == before


@pytest.mark.parametrize('change', ['operation', 'namespace', 'fence'])
def test_mismatched_committed_state_never_opens_reserved_admission(prepared, change):
    spec, fence, backup, target = prepared
    reserve(spec, target)
    import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
    path = target/'migration.json'
    state = json.loads(path.read_text())
    state[change] = 'b'*64 if change == 'fence' else 'other'
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match='reserved for migration'):
        launch(spec, target)


@pytest.mark.parametrize('change', ['symlink', 'oversized', 'malformed', 'extra', 'permissions'])
def test_reservation_corruption_fails_closed(legacy, tmp_path, change):
    target = tmp_path/'agent'
    reserve(legacy, target)
    path = target/'migration-reservation.json'
    if change == 'symlink':
        path.unlink(); path.symlink_to(tmp_path/'missing')
    elif change == 'oversized': path.write_text(' ' * 4097)
    elif change == 'malformed': path.write_text('{')
    elif change == 'extra':
        value = json.loads(path.read_text()); value['unreviewed'] = True
        path.write_text(json.dumps(value))
    else: path.chmod(0o644)
    with pytest.raises((OSError, ValueError)):
        launch(legacy, target)
    assert not (target/'agent-data-format.json').exists()


@pytest.mark.parametrize('failure', ['backup', 'copy'])
def test_workflow_resumes_same_backup_and_reservation(prepared, monkeypatch, failure):
    spec, guard, backup, target = prepared
    guard.close()
    request = dict(protocol=1, operation='move', app='agent', legacy=spec,
                   backup=str(Path(backup['directory']).parent))
    workflow = LocalAgentMigration(request)
    configuration = dict(namespace='migrated-agent', projects=spec['projects'])
    args = dict(target=target, configuration=configuration, owner='owner', node_id='node', request_digest='a'*64)
    if failure == 'backup':
        module = 'pantheon.chatroom.migration_profile.verify_backup'
    else:
        module = 'pantheon.chatroom.migration_import._copy'
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs): raise OSError('interrupted migration')
        patch.setattr(module, fail)
        with pytest.raises(OSError, match='interrupted'):
            workflow._import(**args)
    assert import_reservation(target) is not None
    if failure == 'backup': assert transition_state(target) is None
    with pytest.raises(ValueError): launch(spec, target)
    result = LocalAgentMigration(request)._import(**args)
    assert result['backup'] == backup
    assert result['receipt']['conversations'] == 2
    assert LocalAgentMigration(request)._import(**args) == result
    app = launch(spec, target); app.close()


@pytest.mark.parametrize('failure', ['backup', 'copy'])
def test_owner_abort_releases_sources_but_keeps_destination_unstartable(prepared, monkeypatch, failure):
    from pantheon.chatroom.data_fence import LegacyDataLease
    spec, guard, backup, target = prepared
    guard.close()
    request = dict(protocol=1, operation='move', app='agent', legacy=spec,
                   backup=str(Path(backup['directory']).parent))
    args = dict(target=target, configuration={'namespace': 'migrated-agent'},
                owner='owner', node_id='node', request_digest='a'*64)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs): raise OSError('interrupt')
        patch.setattr('pantheon.chatroom.migration_profile.verify_backup' if failure == 'backup'
                      else 'pantheon.chatroom.migration_import._copy', fail)
        with pytest.raises(OSError): LocalAgentMigration(request)._import(**args)
    result = LocalAgentMigration(request, abort=True)._import(**args)
    assert result['state'] == 'aborted'
    assert import_reservation(target)['phase'] == 'aborted'
    assert LocalAgentMigration(request, abort=True)._import(**args) == result
    with pytest.raises(ValueError): launch(spec, target)
    with pytest.raises(ValueError, match='another migration request'):
        LocalAgentMigration(request)._import(**args)
    lease = LegacyDataLease()
    try: lease.acquire(spec['home_memory'])
    finally: lease.close()
    assert Path(backup['directory']).is_dir()


def test_committed_owner_import_cannot_be_aborted(prepared):
    spec, guard, backup, target = prepared
    guard.close()
    request = dict(protocol=1, operation='move', app='agent', legacy=spec,
                   backup=str(Path(backup['directory']).parent))
    args = dict(target=target, configuration={'namespace': 'migrated-agent'},
                owner='owner', node_id='node', request_digest='a'*64)
    LocalAgentMigration(request)._import(**args)
    state = transition_state(target)
    with pytest.raises(ValueError, match='uncommitted'):
        LocalAgentMigration(request, abort=True)._import(**args)
    assert transition_state(target) == state
    app = launch(spec, target); app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('capability', [None, {}, {'protocol': True, 'dataDirectory': 'agent'},
    {'protocol': 0, 'dataDirectory': 'agent'}, {'protocol': 1, 'dataDirectory': 'other'}])
async def test_owner_rejects_release_without_reservation_admission_before_touching_data(legacy, tmp_path, capability):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pantheon.apps.dependency_assembly import AssemblyError
    request = dict(protocol=1, operation='move', app='agent', legacy=legacy, backup=str(tmp_path/'backup'))
    session = SimpleNamespace(info=SimpleNamespace(node_id='node', fleet_id='owner'),
        runtime=SimpleNamespace(root=tmp_path/'profile'), spec={},
        prepared_app=AsyncMock(return_value={'identity': {'node_id': 'node', 'revision': 'a'*64}}),
        wire=SimpleNamespace(manifest=AsyncMock(return_value={'manifest': {'id': 'agent',
            'caps': {'agentDataInitialization': capability}}})))
    before = user_tree(tmp_path)
    with pytest.raises(AssemblyError, match='reservation is unsupported'):
        await LocalAgentMigration(request)(session)
    assert user_tree(tmp_path) == before
    assert not (tmp_path/'profile').exists()


def test_reservation_fence_identity_preserves_original_path_order(tmp_path):
    from hashlib import sha256
    from pantheon.chatroom.data_fence import fence_identity
    roots = [tmp_path/'a-b', tmp_path/'a/b', tmp_path/'a']
    original = dict(protocol=1, operation='move', namespace='agent', target=str((tmp_path/'target').resolve()),
                    roots=[str(p) for p in sorted({p.resolve() for p in roots})])
    expected = {**original, 'sha256': sha256(json.dumps(original, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()}
    assert fence_identity(roots, operation='move', namespace='agent', target=tmp_path/'target') == expected


@pytest.mark.parametrize('change', ['alias', 'ref', 'endpoint', 'inline-key', 'budget', 'missing-selection'])
def test_owner_credential_request_cannot_provision_unselected_targets(legacy, tmp_path, change):
    binding = dict(provider='openai', source=str(Path(legacy['project_config'])/'settings.json'),
                   alias='connector', ref='node-secret://selected', endpoint='https://provider.example/v1')
    request = dict(protocol=1, operation='move', app='agent', legacy=legacy, backup=str(tmp_path/'backup'),
                   model_selection={'selections': [], 'fleet_tiers': {}},
                   model_credentials={'bindings': [binding]})
    spec = {'model_apps': {'connector': {'app': {'components': {'backend': {'values': {'connector': {
        'endpoint': binding['endpoint'], 'secret_ref': binding['ref']}}}}}}}}
    LocalAgentMigration(request)._check_credential_targets(spec)
    if change == 'alias': binding['alias'] = 'agent'
    elif change == 'ref': binding['ref'] = 'node-secret://unselected'
    elif change == 'endpoint': binding['endpoint'] = 'https://unselected.example/v1'
    elif change == 'inline-key': request['model_credentials']['key'] = 'must-not-copy'
    elif change == 'budget': request['model_credentials']['platform_budget'] = {}
    else: request.pop('model_selection')
    before = user_tree(tmp_path)
    with pytest.raises(ValueError): LocalAgentMigration(request)._check_credential_targets(spec)
    assert user_tree(tmp_path) == before


def test_owner_credential_import_resumes_after_vault_write_without_copying_key_to_agent(
        legacy, tmp_path, endpoint, vault, monkeypatch):
    config, target, fence, backup, bindings = stage(legacy, tmp_path, endpoint, vault)
    fence.close()
    tiers = {tier: 'fleet-route://preserved' for tier in ('low', 'normal', 'high')}
    configuration = config['values']['agent']
    configuration['models'] = {'model_services': 'model_services', 'fleet_tiers': tiers}
    request = dict(protocol=1, operation='model-migration', app='agent', legacy=legacy,
        backup=str(Path(backup['directory']).parent), model_credentials={'bindings': bindings},
        model_selection={'fleet_tiers': tiers, 'selections': [
            {'conversation_id': cid, 'config_id': 'member', 'source': 'openai/fixture',
             'target': 'fleet-route://preserved'} for cid in ('chat-a', 'chat-b')]})
    args = dict(target=target, configuration=configuration, owner='owner', node_id='node',
                request_digest='a'*64, vault=vault)
    ensure = vault.ensure
    def lost_reply(*args):
        ensure(*args)
        raise OSError('Lost vault write reply')
    with monkeypatch.context() as patch:
        patch.setattr(vault, 'ensure', lost_reply)
        with pytest.raises(OSError, match='Lost vault'):
            LocalAgentMigration(request)._import(**args)
    assert transition_state(target)['phase'] == 'importing'
    result = LocalAgentMigration(request)._import(**args)
    assert result['state'] == 'imported'
    assert read_key(vault, bindings[0]['ref'], bindings[0]['endpoint']) == 'legacy-synthetic-key'
    assert LocalAgentMigration(request)._import(**args) == result
    assert 'legacy-synthetic-key' not in json.dumps(result)
    assert 'api_keys' not in json.loads((target/'configuration/.pantheon/settings.json').read_text())
