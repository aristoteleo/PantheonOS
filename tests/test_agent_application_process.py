"""Agent App composition through the generic host and actual local TCP RPC.

The fixture supplies launcher capabilities; it does not substitute a fake Agent
runtime. Final serialized Fleet bootstrap/native Desktop packaging remain gates.
"""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pantheon.remote.backend.tcp import TCPBackend
from pantheon.utils.misc import generate_service_id
from test_agent_application import TEMPLATE, scoped_settings
from test_agent_model_scope import endpoint as model_endpoint
from test_apphost_lifecycle import until


ROOT = Path(__file__).resolve().parents[1]
ENTRY = '''
from pathlib import Path
from pantheon.chatroom.application import AgentApplication
from pantheon.chatroom.app_data import AppProjects
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.instances import AgentInstanceBinding
from pantheon.settings import Settings
from pantheon.utils.model_scope import ModelCallScope

class NoTools:
    async def bind(self, intent):
        assert not intent.config['toolsets'] and not intent.config['mcp_servers']
        return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings())

async def ensure(kind, names):
    if names:
        raise ValueError('No dependency grant in this fixture')

class Entry(AgentApplication):
    def __init__(self, name, workdir, model_url, **kwargs):
        root = Path(workdir)
        data = root/'data'
        settings = Settings(data/'configuration', user_home=data/'user',
            isolated_env=True, environment={'OPENAI_API_KEY': 'process-fixture',
                                           'OPENAI_API_BASE': model_url+'/byok/v1'})
        projects = AppProjects([{'id':'shared', 'name':'Shared', 'path':str(root/'workspace')}],
                               active_id='shared', default_id='shared')
        super().__init__(name, data_dir=data, namespace='process-app', projects=projects,
            settings=settings, model_scope=ModelCallScope(settings), provisioner=NoTools(),
            ensure_services=ensure, validate_model=lambda _: (True, ''), **kwargs)
'''
BOOT = '''
import importlib.abc, runpy, sys
class NoLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name in ('pantheon.chatroom.room', 'pantheon.chatroom.start',
                    'pantheon.platform.service', 'pantheon.repl'):
            raise AssertionError('Agent App imported combined legacy host: '+name)
sys.meta_path.insert(0, NoLegacy())
runpy.run_module('pantheon.apphost', run_name='__main__')
'''


@contextmanager
def process(root, url):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PANTHEON_', 'NATS_', 'FLEET_'))}
    env.update(PYTHONPATH=os.pathsep.join((str(root), str(ROOT))), HOME=str(root/'home'),
        PANTHEON_APPS_ROOT=str(root/'catalog'), PANTHEON_REMOTE_BACKEND='tcp',
        PANTHEON_TCP_REGISTRY=str(root/'registry'), LLM_FORCE_PROXY='true',
        OPENAI_API_KEY='ambient-secret', OPENAI_API_BASE='http://invalid.test/v1')
    with (root/'process.log').open('a') as log:
        child = subprocess.Popen([sys.executable, '-c', BOOT, '--app-id', 'agent-fixture',
            '--workdir', str(root), '--id-hash', 'app-process', '--set', 'model_url='+url],
            cwd=root, env=env, stdout=log, stderr=log)
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.asyncio
async def test_real_agent_process_chat_graceful_restart_and_resume(tmp_path, model_endpoint):
    catalog = tmp_path/'catalog'/'agent-fixture'
    catalog.mkdir(parents=True)
    (catalog/'app.json').write_text(json.dumps({'id':'agent-fixture', 'name':'Agent fixture',
        'version':'1.0.0', 'runtime':'process', 'entry':{'backend':'entry:Entry'},
        'placement':{'requires':['fs:workspace']}}))
    (tmp_path/'entry.py').write_text(ENTRY)
    (tmp_path/'workspace').mkdir()
    scoped_settings(tmp_path/'data')
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': []}]}
    chats, instance = [], None
    for cycle in range(2):
        with process(tmp_path, model_endpoint.url) as child:
            await until(lambda: bool(list((tmp_path/'registry').glob('*.json'))), child)
            service = await TCPBackend(registry_dir=str(tmp_path/'registry')).connect(
                generate_service_id('app-process'))
            try:
                if cycle == 0:
                    for name in ('Run now', 'Unrun metadata'):
                        created = await service.invoke('create_chat', {'chat_name':name,
                            'project_name':'Shared', 'template_obj':template})
                        assert created['success'], created
                        chats.append(created['chat_id'])
                else:
                    listing = await service.invoke('list_chats', {'project_name':'Shared'})
                    assert {chat['id'] for chat in listing['chats']} == set(chats)
                    for chat in chats:
                        saved = await service.invoke('get_chat_template', {'chat_id':chat})
                        assert saved['template']['id'] == template['id'], saved
                info = await service.invoke('get_agents', {'chat_id':chats[0]})
                assert info['success'], info
                identity = info['agents'][0]['instance']['instance_id']
                if instance is not None:
                    assert identity == instance
                instance = identity
                reply = await asyncio.wait_for(service.invoke('chat', {'chat_id':chats[0],
                    'message':[{'role':'user', 'content':f'turn {cycle}'}]}), 15)
                assert reply['success'], reply
                history = await service.invoke('get_chat_messages', {'chat_id':chats[0]})
                assert history['success'], history
                assert sum(m.get('role') == 'user' for m in history['messages']) == cycle+1
                assert 'scoped reply' in json.dumps(history)
            finally:
                await service.close()
            child.terminate()
            assert await asyncio.to_thread(child.wait, 10) == 0, (tmp_path/'process.log').read_text()
            assert not list((tmp_path/'registry').glob('*.json'))
    assert len(model_endpoint.requests) == 2
    assert all(headers['Authorization'] == 'Bearer process-fixture'
               for _, headers, _ in model_endpoint.requests)
