import pytest

from pantheon.models.manager import ModelServiceManager
from pantheon.platform import platform_catalog

TIER = {'id': 'openrouter/openai/gpt-5.5', 'name': 'openrouter/openai/gpt-5.5', 'operations': ['text'],
        'compute': 'provider', 'tools': True, 'context': 200000, 'context_limit': 200000}


class Directory:
    def __init__(self, row):
        self.row = row

    async def deployment(self, deployment_id):
        assert deployment_id == 'platform'
        return dict(self.row)


class Manager:
    chat_entry = staticmethod(ModelServiceManager.chat_entry)

    def __init__(self, row, discovered):
        self.client, self.discovered, self.published = Directory(row), discovered, None

    async def rpc(self, binding, method):
        assert method == 'discover'
        return self.discovered

    async def publish(self, deployment_id, models, revision):
        self.published = (deployment_id, models, revision)


@pytest.fixture
def suggestions(monkeypatch):
    known = {'openrouter/anthropic/claude-opus-5.5': dict(context=1000000, tools=True, vision=True),
             'openrouter/qwen/qwen-image': dict(context=32000, tools=False),
             'openrouter/openai/gpt-5.5': dict(context=400000, tools=True)}

    async def suggest(ids, provider='', client=None):
        return {m: known[m] for m in ids if m in known}
    monkeypatch.setattr('pantheon.models.model_metadata.suggest', suggest)


@pytest.mark.asyncio
async def test_publishes_only_discovered_tool_models_and_keeps_owner_choices(suggestions):
    row = {'engine': 'api', 'state': 'ready', 'binding': {}, 'revision': 7, 'models': [TIER]}
    discovered = {'models': [{'id': 'openrouter/openai/gpt-5.5'}, {'id': 'openrouter/anthropic/claude-opus-5.5'},
                             {'id': 'openrouter/qwen/qwen-image'}, {'id': 'openrouter/meta/unknown'},
                             {'id': 'openrouter/mistral/withdrawn'}]}
    manager = Manager(row, discovered)
    curated = ['openrouter/openai/gpt-5.5', 'openrouter/anthropic/claude-opus-5.5', 'openrouter/qwen/qwen-image',
               'openrouter/meta/unknown', 'openrouter/mistral/withdrawn', 'openrouter/not/discovered']
    added = await platform_catalog.publish_curated(manager, curated, withdrawn={'openrouter/mistral/withdrawn'})
    assert added == 1
    deployment, models, revision = manager.published
    assert (deployment, revision) == ('platform', 7)
    assert models[0] == TIER  # the owner's tier publication is untouched
    assert models[1]['id'] == 'openrouter/anthropic/claude-opus-5.5' and models[1]['tools'] is True
    assert models[1]['context'] == 1000000


@pytest.mark.asyncio
async def test_nothing_missing_or_not_the_platform_connector_publishes_nothing(suggestions):
    row = {'engine': 'api', 'state': 'ready', 'binding': {}, 'revision': 1, 'models': [TIER]}
    manager = Manager(row, {'models': [{'id': TIER['id']}]})
    assert await platform_catalog.publish_curated(manager, [TIER['id']]) == 0
    manager = Manager({**row, 'engine': 'vllm'}, {'models': [{'id': 'x'}]})
    assert await platform_catalog.publish_curated(manager, ['x']) == 0
    assert manager.published is None


@pytest.mark.asyncio
async def test_platform_reregistration_is_not_an_owner_withdrawal(tmp_path, monkeypatch):
    from pantheon.platform.service import PlatformService
    import pantheon.platform.first_run as first_run
    service = PlatformService()
    service._owner_state_directory = tmp_path
    service._write_private('platform-catalog.json', {'offered': ['openrouter/anthropic/claude-opus-5.5']})
    retired = []

    async def retire(directory, states, ids, node):
        retired.append((ids, node))
    monkeypatch.setattr(first_run, 'retire_stale_registrations', retire)
    class Directory:
        async def deployments(self):
            return [{'deployment_id': 'platform', 'models': [{'id': 'owner/kept'}]}]
    monkeypatch.setattr(service, '_model_services_manager', lambda: type('M', (), {'client': Directory()})())
    await service._retire_registrations({}, ['other'], 'n_x')
    assert service._read_private('platform-catalog.json')['offered']  # another deployment: kept
    await service._retire_registrations({}, ['platform'], 'n_x')
    assert service._read_private('platform-catalog.json') == {'offered': []}
    # The owner's published models are offered again once the new registration runs.
    assert service._read_private('platform-published.json') == {'models': ['owner/kept']}
    assert retired == [(['other'], 'n_x'), (['platform'], 'n_x')]


@pytest.mark.asyncio
async def test_tier_routes_are_seeded_once_and_then_owner_edits_are_kept():
    saved = []

    class Client:
        def __init__(self, routes):
            self._routes = routes

        async def deployment(self, deployment_id):
            return {'node_id': 'n_brain', 'models': [{'id': m} for m in ('a', 'b', 'c')]}

        async def routes(self):
            return self._routes

        async def route_operation(self, action, route):
            saved.append((action, route))

    manager = type('M', (), {})()
    manager.client = Client([])
    tiers = {'normal': ['a', 'missing', 'b'], 'low': ['missing'], 'high': 'old-single-model'}
    assert await platform_catalog.ensure_tier_routes(manager, tiers) == ['tier-normal']
    action, route = saved[0]
    assert action == 'save' and route['fallback'] == 'failover' and route['revision'] == 0
    assert [c['model_id'] for c in route['candidates']] == ['a', 'b'] and route['allowed_nodes'] == ['n_brain']
    assert route['name'] == 'Normal'  # the Agent's tier name
    owner = {**route, 'candidates': [{'deployment_id': 'platform', 'model_id': 'c'}], 'revision': 4}
    saved.clear()
    manager.client = Client([owner])
    assert await platform_catalog.ensure_tier_routes(manager, tiers) == []
    manager.client = Client([{**owner, 'allowed_nodes': ['n_old']}])
    assert await platform_catalog.ensure_tier_routes(manager, tiers) == ['tier-normal']
    assert saved[0][1]['candidates'] == owner['candidates'] and saved[0][1]['allowed_nodes'] == ['n_brain']
    saved.clear()
    manager.client = Client([{**owner, 'name': 'Normal quality'}])  # an earlier seed's name
    assert await platform_catalog.ensure_tier_routes(manager, tiers) == ['tier-normal']
    assert saved[0][1]['name'] == 'Normal' and saved[0][1]['candidates'] == owner['candidates']
