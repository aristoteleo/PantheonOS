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
