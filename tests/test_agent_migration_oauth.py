"""Scoped OAuth migration through backup, owner command and Agent admission."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.data_transition import transition_state, INITIALIZATION_CAPABILITY
from pantheon.chatroom.migration_oauth import OAuthConfigurationConversion
from pantheon.chatroom.migration_profile import LocalAgentMigration
from pantheon.utils.local_data_ownership import DataFencedError
from pantheon.utils.oauth import CodexOAuthManager, GeminiCliOAuthManager
from test_agent_migration import legacy
from test_agent_migration_import import prepared


@pytest.fixture(autouse=True)
def oauth_source(legacy, request, monkeypatch):
    root = Path(legacy['global_config'])
    records = {
        'codex': {'provider': 'codex', 'tokens': {'access_token': 'synthetic-access',
                   'refresh_token': 'synthetic-codex-refresh', 'account_id': 'test-account'}},
        'gemini-cli': {'provider': 'gemini_cli', 'tokens': {'access_token': 'synthetic-access',
                   'refresh_token': 'synthetic-gemini-refresh', 'expires_at': 9999999999,
                   'project_id': 'test-project', 'email': 'test@example.invalid'}},
    }
    bad = getattr(request, 'param', None)
    if bad == 'mixed-models':
        from test_agent_migration_import import RECIPE
        monkeypatch.setitem(RECIPE, 'model', ['codex/gpt-fixture+think:high', 'openai/fixture'])
    if bad == 'provider': records['codex']['provider'] = 'other'
    elif bad == 'unknown-token': records['codex']['tokens']['unreviewed'] = 'value'
    elif bad == 'no-refresh': records['codex']['tokens'].pop('refresh_token')
    elif bad == 'nested-token': records['codex']['tokens']['account_id'] = {'unexpected': True}
    elif bad == 'expiry': records['gemini-cli']['tokens']['expires_at'] = True
    elif bad == 'unknown-config': records['codex']['credentials'] = {'api-key': 'do-not-copy'}
    for provider, name, cls in [('codex', 'codex.json', CodexOAuthManager),
                                ('gemini-cli', 'gemini_cli.json', GeminiCliOAuthManager)]:
        cls(root / 'oauth' / name, ownership_root=root)._save(records[provider])
    return records


def inputs(prepared):
    spec, fence, backup, root = prepared
    fence.close()
    request = dict(protocol=1, operation='move', app='agent', legacy=spec,
                   backup=str(Path(backup['directory']).parent),
                   oauth_configuration={'providers': ['codex', 'gemini-cli']})
    return request, dict(target=root, configuration={'namespace': 'migrated-agent',
        'models': {'oauth': ['codex', 'gemini-cli']}}, owner='owner', node_id='node', request_digest='a'*64)


def open_app(prepared, models=None):
    spec, _, _, root = prepared
    return AgentAppData(root, namespace='migrated-agent', projects=AppProjects(spec['projects']),
        model_configuration=models or {'owner': 'owner', 'node_id': 'node',
                                       'models': {'oauth': ['codex', 'gemini-cli']}})


def test_owner_migrates_private_logins_and_retry_preserves_rotation(prepared, oauth_source, monkeypatch):
    request, args = inputs(prepared)
    migration = LocalAgentMigration(request)
    result = migration._import(**args)
    root = args['target']
    assert result['state'] == 'imported'
    app = open_app(prepared)
    app.close()
    receipt = json.dumps(result)
    assert 'synthetic-access' not in receipt and 'synthetic-codex-refresh' not in receipt
    assert result['receipt']['oauth_bindings']['providers'] == ['codex', 'gemini-cli']
    assert not any(row['target'].startswith('oauth/') for row in result['receipt']['files'])
    for provider, cls in [('codex', CodexOAuthManager), ('gemini-cli', GeminiCliOAuthManager)]:
        path = root / 'oauth' / (provider + '.json')
        manager = cls(path, ownership_root=root)
        assert manager.get_tokens() == oauth_source[provider]['tokens']
        assert path.stat().st_mode & 0o077 == 0
        def refreshed(token):
            assert token == oauth_source[provider]['tokens']['refresh_token']
            return {'access_token': 'fresh-access', 'refresh_token': 'rotated-at-destination',
                    **({'id_token': 'synthetic.id.token'} if provider == 'codex' else {'expires_at': 9999999999})}
        monkeypatch.setattr('pantheon.utils.oauth.' + ('codex._refresh_tokens' if provider == 'codex'
                            else 'gemini.refresh_access_token'), refreshed)
        manager.refresh()
    assert migration._import(**args) == result
    assert CodexOAuthManager(root / 'oauth/codex.json', ownership_root=root).get_tokens()['refresh_token'] == 'rotated-at-destination'
    source = Path(request['legacy']['global_config'])
    with pytest.raises(DataFencedError):
        CodexOAuthManager(source / 'oauth/codex.json', ownership_root=source).get_tokens()


@pytest.mark.parametrize('oauth_source', ['provider', 'unknown-token', 'no-refresh', 'nested-token',
                                       'expiry', 'unknown-config'], indirect=True)
def test_unknown_or_invalid_credential_formats_are_not_admitted(prepared):
    request, args = inputs(prepared)
    with pytest.raises(ValueError, match='format recovery'):
        LocalAgentMigration(request)._import(**args)
    assert transition_state(args['target']) is None
    assert not (args['target'] / 'oauth').exists()


@pytest.mark.parametrize('abort', [False, True])
def test_partial_credentials_never_admit_agent_and_can_resume_or_abort(prepared, monkeypatch, abort):
    import pantheon.chatroom.migration_import as importer
    request, args = inputs(prepared)
    original = importer._copy

    def fail_second(snapshot, root, item):
        if item['target'] == 'oauth/gemini-cli.json':
            raise OSError('injected credential delivery failure')
        return original(snapshot, root, item)

    with monkeypatch.context() as patch:
        patch.setattr(importer, '_copy', fail_second)
        with pytest.raises(OSError, match='injected'):
            LocalAgentMigration(request)._import(**args)
    assert transition_state(args['target'])['phase'] == 'importing'
    with pytest.raises(ValueError): open_app(prepared)
    result = LocalAgentMigration(request, abort=abort)._import(**args)
    if abort:
        assert result['state'] == 'aborted'
        with pytest.raises(ValueError): open_app(prepared)
        source = Path(request['legacy']['global_config'])
        assert CodexOAuthManager(source / 'oauth/codex.json', ownership_root=source).get_tokens()
    else:
        assert result['state'] == 'imported'
        app = open_app(prepared)
        app.close()


@pytest.mark.parametrize('change', ['providers', 'owner', 'node', 'receipt'])
def test_launch_cannot_change_imported_oauth_binding(prepared, change):
    request, args = inputs(prepared)
    LocalAgentMigration(request)._import(**args)
    models = {'owner': 'owner', 'node_id': 'node', 'models': {'oauth': ['codex', 'gemini-cli']}}
    if change == 'providers': models['models']['oauth'] = ['codex']
    elif change == 'owner': models['owner'] = 'other'
    elif change == 'node': models['node_id'] = 'other'
    else: (args['target'] / 'migration-oauth-bindings.json').write_text('{}')
    with pytest.raises(ValueError, match='OAuth bindings'):
        open_app(prepared, models)


def test_unselected_provider_is_not_silently_discarded(prepared):
    request, args = inputs(prepared)
    request['oauth_configuration']['providers'] = ['codex']
    args['configuration']['models']['oauth'] = ['codex']
    with pytest.raises(ValueError, match='explicit converter'):
        LocalAgentMigration(request)._import(**args)
    assert transition_state(args['target']) is None


@pytest.mark.parametrize('oauth_source', ['mixed-models'], indirect=True)
def test_mixed_model_routes_preserve_oauth_fallback_and_reasoning(prepared):
    request, args = inputs(prepared)
    tiers = {tier: 'fleet-route://preserved' for tier in ('low', 'normal', 'high')}
    request['model_selection'] = {'fleet_tiers': tiers, 'selections': [
        {'conversation_id': cid, 'config_id': 'original-member',
         'source': ['codex/gpt-fixture+think:high', 'openai/fixture'],
         'target': ['codex/gpt-fixture+think:high', 'fleet-route://preserved']}
        for cid in ('chat-a', 'chat-b')]}
    models = {'oauth': ['codex', 'gemini-cli'], 'model_services': 'model_services', 'fleet_tiers': tiers}
    args['configuration']['models'] = models
    result = LocalAgentMigration(request)._import(**args)
    assert result['receipt']['model_bindings']['models'] == models
    for member in result['receipt']['members']:
        path = next((args['target']/'conversations').rglob(member['conversation_id'] + '*.json'))
        saved = json.loads(path.read_text())['extra_data']['team_template']['agents'][0]
        assert saved['model'] == ['codex/gpt-fixture+think:high', 'fleet-route://preserved']
    app = open_app(prepared, {'owner': 'owner', 'node_id': 'node', 'models': models})
    app.close()


@pytest.mark.parametrize('source,target,oauth', [
    ('codex/old', 'codex/new', ['codex']),
    ('codex/old', 'codex/old', []),
    ('gemini-cli/old', 'gemini-cli/old', ['codex']),
    ('codex/old+think:high', 'codex/old+think:low', ['codex']),
])
def test_oauth_preservation_cannot_change_model_or_billing_implicitly(source, target, oauth):
    from pantheon.chatroom.migration_models import _validate_pair
    with pytest.raises(ValueError): _validate_pair(source, target, oauth=oauth)


@pytest.mark.asyncio
@pytest.mark.parametrize('oauth_source', ['mixed-models'], indirect=True)
async def test_budget_review_only_uses_fleet_part_of_explicit_mixed_fallback(prepared, monkeypatch):
    from pantheon.chatroom.migration_models import ModelSelectionConversion
    from pantheon.chatroom.migration_import import import_backup
    spec, fence, backup, root = prepared
    oauth = OAuthConfigurationConversion(backup['directory'], digest=backup['sha256'], fence=fence,
        owner='owner', node_id='node', providers=['codex', 'gemini-cli'])
    selection = ModelSelectionConversion(backup['directory'], digest=backup['sha256'], fence=fence,
        owner='owner', node_id='node', oauth=['codex', 'gemini-cli'],
        fleet_tiers={tier: 'fleet-route://preserved' for tier in ('low', 'normal', 'high')},
        selections=[dict(conversation_id=cid, config_id='original-member',
            source=['codex/gpt-fixture+think:high', 'openai/fixture'],
            target=['codex/gpt-fixture+think:high', 'fleet-route://preserved']) for cid in ('chat-a', 'chat-b')])

    async def review(client, provisioning, references):
        assert references == ['fleet-route://preserved']
        return {'model_selection': {'selected': [{'reference': 'fleet-route://preserved'}]}}

    monkeypatch.setattr('pantheon.chatroom.migration_budget.review_budget_models', review)
    await selection.review_budget(object(), {})
    with pytest.raises(ValueError, match='paired credential'):
        import_backup(backup['directory'], digest=backup['sha256'], fence=fence, model_selection=selection)
    result = import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
        model_selection=selection, oauth_configuration=oauth)
    assert result['oauth_bindings']['providers'] == ['codex', 'gemini-cli']


def test_credentials_cannot_be_delivered_before_import_barrier(prepared):
    _, fence, backup, root = prepared
    conversion = OAuthConfigurationConversion(backup['directory'], digest=backup['sha256'], fence=fence,
        owner='owner', node_id='node', providers=['codex', 'gemini-cli'])
    with pytest.raises(ValueError, match='unstartable'):
        conversion.provision(root)
    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('capability', [None, {}, {'protocols': [True]}, {'protocols': '1'}])
async def test_old_release_rejected_before_oauth_import(legacy, tmp_path, capability):
    from pantheon.apps.dependency_assembly import AssemblyError
    request = dict(protocol=1, operation='oauth', app='agent', legacy=legacy, backup=str(tmp_path/'backup'),
                   oauth_configuration={'providers': ['codex']})
    session = SimpleNamespace(info=SimpleNamespace(node_id='node'),
        prepared_app=AsyncMock(return_value={'identity': {'node_id': 'node', 'revision': 'a'*64}}),
        wire=SimpleNamespace(manifest=AsyncMock(return_value={'manifest': {'id': 'agent', 'caps': {
            'agentDataInitialization': INITIALIZATION_CAPABILITY, 'agentOAuthMigration': capability}}})))
    with pytest.raises(AssemblyError, match='scoped OAuth admission'):
        await LocalAgentMigration(request)(session)
    assert not (tmp_path/'backup').exists()
