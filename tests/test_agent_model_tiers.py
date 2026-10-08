"""Owner-selected model tiers consume the existing authorized Model Services.

The TLS gateway and connector are real; inference responses are deterministic.
No ambient or direct provider may substitute for an unavailable bound tier.
"""
import ssl

import pytest

from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.app_models import AppModels
from test_model_dependency import model_dependency, model_endpoint, tls_material


@pytest.mark.asyncio
@pytest.mark.parametrize('ref', ['fleet-model://mac/example%3A8b', 'fleet-route://local'])
async def test_tiers_use_authorized_catalog_and_existing_inference(tmp_path, model_dependency, model_endpoint, monkeypatch, ref):
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    config = {'model_services': 'models', 'fleet_tiers': {'normal': ref, 'low': ref},
              'providers': {'openai': 'byok'}}
    models = AppModels(tmp_path / 'app', defaults={}, config=config,
        credentials={'models': RuntimeCredential(**model_dependency.credential),
                     'byok': RuntimeCredential('https://must-not-call.invalid/v1', 'explicit-byok')},
        tls_context=ssl.create_default_context(cafile=str(tmp_path / 'cert.pem')))
    try:
        with pytest.raises(ValueError, match='unavailable'):
            models.resolve(None)  # No unconfirmed catalog at startup.
        await models.refresh()
        config['fleet_tiers']['normal'] = 'fleet-route://unapproved'
        for tag in (None, 'normal', 'low', 'normal,tools'):
            assert models.scope.models(tag) == [ref]
            assert models.validate(tag) == (True, '')
        assert models.catalog()['fleet_tiers']['normal'] == ref
        with pytest.raises(ValueError, match='unavailable'):
            models.resolve('high')  # Missing tier does not fall back to BYOK.
        with pytest.raises(ValueError, match='capabilities'):
            models.resolve('normal,pdf')  # Unconfirmed capability is not enough.
        from pantheon.agent import Agent
        agent = Agent(name='Tier agent', instructions='Reply once', model='normal', model_scope=models.scope)
        assert (await agent.run('Reply once')).content == 'scoped reply'
        inference = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
        assert len(inference) == 1 and inference[0]['model'] == 'example:8b'
        model_dependency.revoked = True
        await models.refresh()
        assert not models.validate('normal')[0]
        with pytest.raises(ValueError, match='unavailable'):
            models.scope.models(None)
        # Explicitly selected compatibility models remain a separate choice.
        assert models.validate('openai/gpt-5.4') == (True, '')
    finally:
        await models.aclose()


@pytest.mark.parametrize('tiers', [None, {}, [], {'other': 'fleet-route://local'},
    {'normal': ''}, {'normal': ['fleet-route://local']}, {'normal': 'openai/gpt-5.4'},
    {'normal': 'fleet-model://missing-model'}, {'normal': 'fleet-route://bad/path'}])
def test_invalid_tiers_rejected_before_private_settings_creation(tmp_path, tiers):
    with pytest.raises(ValueError):
        AppModels(tmp_path / 'app', defaults={},
            config={'model_services': 'models', 'fleet_tiers': tiers},
            credentials={'models': RuntimeCredential('https://gateway.test/rpc', 'a' * 64)})
    assert not (tmp_path / 'app').exists()


def test_tiers_require_dependency_and_never_use_process_fleet_client(tmp_path):
    with pytest.raises(ValueError, match='binding'):
        AppModels(tmp_path, defaults={}, config={'fleet_tiers': {'normal': 'fleet-route://local'}}, credentials={})


@pytest.mark.asyncio
async def test_failover_tier_route_becomes_the_owner_ordered_chain(tmp_path):
    first, second, closed = ('fleet-model://platform/openrouter%2F' + m for m in ('a%2Fopen', 'b%2Fopen', 'c%2Fclosed'))

    class Fleet:
        metadata = {}
        fallback = 'failover'

        async def catalog(self):
            source = {'id': 'platform', 'available': True}
            return [source], [dict(model=m, name=m, source='platform', operations=['text'],
                                   capabilities={'tools': True}, context=200000) for m in (first, second, closed)]

        async def hub_request(self, method, path, data):
            assert (method, path, data) == ('POST', '/api/model-services/routes/tier-normal/resolve',
                                            {'operation': 'text', 'tools': True})
            ids = ['openrouter/a/open', 'openrouter/b/open', 'openrouter/c/closed']
            return {'route': {'fallback': self.fallback},
                    'candidates': [{'deployment': {'deployment_id': 'platform'}, 'model': {'id': i}} for i in ids]}

        async def aclose(self):
            pass

    models = AppModels(tmp_path / 'app', defaults={},
        config={'model_services': 'models', 'fleet_tiers': {'normal': 'fleet-route://tier-normal'}},
        credentials={'models': RuntimeCredential('https://gateway.test/rpc', 'a' * 64)})
    models._owned_fleet = fleet = Fleet()
    await models.refresh()
    assert models.scope.models('normal') == [first, second, closed]
    assert models.catalog()['fleet_tier_chains'] == {'normal': [first, second, closed]}
    fleet.fallback = 'preflight'  # A Model Services choice, not an Agent chain.
    await models.refresh()
    assert models.catalog()['fleet_tier_chains'] == {}
