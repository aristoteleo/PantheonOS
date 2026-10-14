"""A template's `apps` field: which Apps an agent may use
(docs/pantheonos-app-interface.md)."""
from types import SimpleNamespace

import pytest

from pantheon.apps.agent_defaults import with_app_filter
from pantheon.factory.instances import _config
from pantheon.factory.models import AgentConfig
from pantheon.factory.provisioned_instances import ProvisionedAgentInstanceFactory
from pantheon.factory.template_io import UnifiedMarkdownParser
from test_agent_instance_factory import RECIPE

DEPLOYMENT = ('file_manager', 'shell', 'integrated_notebook', 'web', 'evolution', 'desktop', 'fleet',
              'model_services')


def test_a_template_agent_reads_and_writes_apps():
    parser = UnifiedMarkdownParser()
    agent = parser.parse_agent('---\nid: w\nname: W\napps:\n  allow: [web, file_manager]\n---\nHi\n')
    assert agent.apps == {'allow': ['file_manager', 'web']}
    text = parser.generate_agent(agent)
    assert 'apps:' in text and 'toolsets' not in text
    with pytest.raises(ValueError):
        parser.parse_agent('---\nid: w\nname: W\napps: [web]\n---\nHi\n')


def test_an_agent_without_apps_or_toolsets_may_use_every_app_and_toolsets_stay_legacy():
    assert AgentConfig(id='a', name='A', model='m').to_creation_payload()['apps'] == {}
    builtins = AgentConfig(id='a', name='A', model='m', toolsets=['task', 'mcp:docs']).to_creation_payload()
    assert builtins['apps'] == {} and builtins['toolsets'] == ['mcp:docs']
    legacy = AgentConfig(id='a', name='A', model='m', toolsets=['shell', 'task']).to_creation_payload()
    assert 'apps' not in legacy and legacy['toolsets'] == ['shell', 'task']
    both = AgentConfig(id='a', name='A', model='m', toolsets=['shell', 'mcp:docs'],
                       apps={'deny': ['web']}).to_creation_payload()
    assert both['apps'] == {'deny': ['web']} and both['toolsets'] == ['mcp:docs']


def test_the_filter_selects_the_deployment_apps_to_bind():
    def toolsets(apps, toolsets=()):
        return with_app_filter(_config({**RECIPE, 'toolsets': list(toolsets), 'apps': apps})[0],
                               DEPLOYMENT)['toolsets']
    assert toolsets({}) == list(DEPLOYMENT)
    assert toolsets({'deny': ['evolution', 'fleet']}) == [n for n in DEPLOYMENT if n not in ('evolution', 'fleet')]
    # Only deployment Apps allowed: no Desktop, since no library App can pass.
    assert toolsets({'allow': ['web', 'file_manager']}) == ['file_manager', 'web']
    # A library App reached through the Desktop binds it (its own tools stay hidden).
    assert toolsets({'allow': ['web', 'game-of-life']}) == ['web', 'desktop']
    assert toolsets({'allow': ['web']}, ['mcp:docs']) == ['mcp:docs', 'web']


def test_prepared_recipes_use_the_filter_or_the_legacy_defaults():
    factory = ProvisionedAgentInstanceFactory.__new__(ProvisionedAgentInstanceFactory)
    factory._defaults = {'toolsets': [], 'mcp_servers': [], 'primary_toolsets': ['fleet']}
    factory._deployment_apps = DEPLOYMENT
    prepared = factory.prepare_configs({
        'leader': {**RECIPE, 'name': 'Leader', 'apps': {}},
        'worker': {**RECIPE, 'name': 'Worker', 'apps': {'allow': ['shell']}},
        'old': {**RECIPE, 'name': 'Old', 'toolsets': ['shell']},
    })
    assert prepared['leader']['toolsets'] == list(DEPLOYMENT) and prepared['leader']['apps'] == {}
    assert prepared['worker']['toolsets'] == ['shell']
    assert prepared['old']['toolsets'] == ['shell'] and 'apps' not in prepared['old']


def test_a_recipe_without_apps_keeps_its_revision():
    assert _config({**RECIPE, 'apps': None})[1] == _config(RECIPE)[1]
    assert _config({**RECIPE, 'apps': {}})[1] != _config(RECIPE)[1]
    with pytest.raises(ValueError):
        _config({**RECIPE, 'apps': {'allow': 'web'}})


@pytest.mark.asyncio
async def test_a_bound_desktop_the_filter_excludes_only_reaches_library_apps():
    from pantheon.agent import Agent
    from pantheon.agent_pantheonos import AppFilter
    from pantheon.dependency_provider import DependencyToolProvider
    from pantheon.factory.bindings import AgentToolBindings

    class Provider(DependencyToolProvider):
        def __init__(self, name):
            self.toolset_name = name

        async def initialize(self):
            pass

        async def list_tools(self):
            return []

    bindings = AgentToolBindings(toolsets={'web': Provider('web'), 'desktop': Provider('desktop')})
    agent = Agent(name='w', instructions='', model='gpt-4o-mini')
    agent.app_filter = AppFilter(allow=['web', 'game-of-life'])
    await bindings.attach(agent, ['web', 'desktop'], [])
    assert set(agent.providers) == {'web'} and agent.app_directory.desktop is bindings.toolsets['desktop']
