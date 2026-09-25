import asyncio

import pytest

from pantheon.internal.model_services_plugin import ModelServicesPlugin, ModelServicesToolSet


class Agent:
    def __init__(self, name):
        self.name, self.models, self.instructions = name, ['openai/gpt-5.5'], 'You lead.'


class Team:
    def __init__(self):
        self.team_agents = [Agent('Leader'), Agent('coder')]


class Client:
    async def deployment(self, deployment_id):
        return dict(deployment_id=deployment_id, state='ready', models=[
            dict(id='qwen', tools=True, context=65536), dict(id='plain', tools=None, context=65536)])


class Manager:
    client = Client()
    resolver = None


def test_agent_tools_are_visible_and_gpu_launch_needs_approval(monkeypatch):
    toolset = ModelServicesToolSet(Team())
    # tool_functions is what the LLM sees (exclude=True tools are filtered out).
    assert {'model_services_overview', 'modal_gpu_start', 'modal_gpu_status', 'modal_gpu_stop',
            'model_service_set_running', 'use_fleet_model'} <= set(toolset.tool_functions)
    launched = []

    async def start(*args):
        launched.append(args)
        return {'phase': 'starting_node'}
    from pantheon.models import modal_gpu
    monkeypatch.setattr(modal_gpu, 'start', start)

    async def manager():
        return Manager()
    monkeypatch.setattr(toolset, '_m', manager)
    refused = asyncio.run(toolset.modal_gpu_start('qwen36'))
    assert refused['started'] is False and 'notify_user' in refused['message'] and not launched
    assert asyncio.run(toolset.modal_gpu_start('qwen36', user_confirmed=True))['phase'] == 'starting_node'
    assert launched[0][1:] == ('qwen36', 'qwen3.6-35b-a3b-fp8', 'H100', 240)


def test_use_fleet_model_switches_only_to_ready_tool_capable_models(monkeypatch):
    team = Team()
    toolset = ModelServicesToolSet(team)

    async def manager():
        return Manager()
    monkeypatch.setattr(toolset, '_m', manager)
    with pytest.raises(ValueError, match='Tools supported'):
        asyncio.run(toolset.use_fleet_model('fleet-model://modal-qwen36/plain'))
    result = asyncio.run(toolset.use_fleet_model('fleet-model://modal-qwen36/qwen', 'coder'))
    assert result['agent'] == 'coder' and team.team_agents[1].models == ['fleet-model://modal-qwen36/qwen']
    assert team.team_agents[0].models == ['openai/gpt-5.5']


def test_plugin_injects_only_when_fleet_is_configured(monkeypatch):
    team = Team()
    monkeypatch.delenv('FLEET_CONTROLLER_URL', raising=False)
    monkeypatch.delenv('FLEET_NATS_URL', raising=False)
    assert asyncio.run(ModelServicesPlugin().get_toolsets(team)) == []
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://fleet.example')
    [(toolset, agents)] = asyncio.run(ModelServicesPlugin().get_toolsets(team))
    assert agents == ['Leader'] and isinstance(toolset, ModelServicesToolSet)
    asyncio.run(ModelServicesPlugin().on_team_created(team))
    assert 'notify_user' in team.team_agents[0].instructions
