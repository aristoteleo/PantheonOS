"""Durable dynamic assembly with actual Agents, Teams and HTTPS tool clients.

The provisioner is a local fixture representing already granted capabilities;
these tests do not claim a live platform allocator or distributed data fencing.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.chatroom.environment import AgentEnvironment
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.instance_store import AgentInstanceStore
from pantheon.factory.instances import AgentInstanceBinding
from pantheon.factory.models import TeamConfig
from pantheon.factory.provisioned_instances import ProvisionedAgentInstanceFactory
from test_agent_dependency_bindings import endpoint, forbid_ambient_tools, provider
from test_agent_instance_factory import RECIPE
from test_agent_model_scope import scopes


def store(tmp_path):
    return AgentInstanceStore(tmp_path / 'instances', namespace='app-data')


class Provisioner:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.calls, self.delivered, self.operations = [], [], {}
        self.fail_once = False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    async def bind(self, intent):
        self.calls.append(intent)
        token = self.operations.setdefault(intent.instance_id, 'ab'[len(self.operations) % 2] * 64)
        self.entered.set()
        await self.release.wait()
        if self.fail_once:
            self.fail_once = False
            # Model a lost reply after resource acquisition, before delivery.
            raise TimeoutError('resource reply lost')
        binding = AgentInstanceBinding(**intent.identity(),
            tools=AgentToolBindings({'shell': provider(self.endpoint, token=token)}))
        self.delivered.append(binding)
        return binding


def factory(tmp_path, endpoint, scope):
    p = Provisioner(endpoint)
    return ProvisionedAgentInstanceFactory(store(tmp_path), p, model_scope=scope), p


def test_store_restart_revisions_and_concurrent_reservations(tmp_path):
    original = store(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            same = list(pool.map(lambda _: original.reserve('chat', {'member': RECIPE})[0], range(12)))
        assert len({v.instance_id for v in same}) == len({v.operation_id for v in same}) == 1
        first = same[0]
        with pytest.raises(TypeError):
            first.config['instructions'] = 'changed'
        edited = original.reserve('chat', {'member': {**RECIPE, 'instructions': 'new'}})[0]
        other = original.reserve('other-chat', {'member': RECIPE})[0]
        assert edited.instance_id == first.instance_id != other.instance_id
        assert edited.operation_id != first.operation_id
        assert edited.config_revision != first.config_revision
        with pytest.raises(TimeoutError):
            store(tmp_path)
    finally:
        original.close()
    reopened = store(tmp_path)
    try:
        assert reopened.reserve('chat', {'member': RECIPE})[0] == first
        assert reopened.reserve('chat', {'member': {**RECIPE, 'instructions': 'new'}})[0] == edited
    finally:
        reopened.close()
    with pytest.raises(ValueError, match='namespace'):
        AgentInstanceStore(tmp_path / 'instances', namespace='other-app')
    store(tmp_path).close()  # Failed open released its writer lock.


def test_identity_journal_survives_a_separate_process(tmp_path):
    writer = store(tmp_path)
    intent = writer.reserve('persisted-chat', {'member': RECIPE})[0]
    writer.close()
    code = """
import json, sys
from pantheon.factory.instance_store import AgentInstanceStore
s = AgentInstanceStore(sys.argv[1], namespace='app-data')
try:
    i = s.reserve('persisted-chat', {'member': json.loads(sys.argv[2])})[0]
    print(json.dumps({**i.identity(), 'operation_id': i.operation_id}))
finally:
    s.close()
"""
    child = subprocess.run([sys.executable, '-c', code, str(tmp_path / 'instances'), json.dumps(RECIPE)],
        cwd=tmp_path, env={'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
                          'PATH': os.environ.get('PATH', ''), 'PYTHONDONTWRITEBYTECODE': '1'},
        text=True, capture_output=True, timeout=20)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {**intent.identity(), 'operation_id': intent.operation_id}
    store(tmp_path).close()


def test_failed_initialization_rolls_back_schema_and_leaves_writer_reusable(tmp_path, monkeypatch):
    import pantheon.factory.instance_store as module
    connect = sqlite3.connect
    class Broken(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith('CREATE TABLE revisions'):
                raise RuntimeError('simulated disk failure')
            return super().execute(sql, *args)
    monkeypatch.setattr(module.sqlite3, 'connect', lambda *a, **k: connect(*a, **k, factory=Broken))
    with pytest.raises(RuntimeError, match='disk failure'):
        store(tmp_path)
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    with connect(tmp_path / 'instances' / 'instances.sqlite3') as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
    store(tmp_path).close()


def test_unsupported_schema_not_replaced(tmp_path):
    store(tmp_path).close()
    path = tmp_path / 'instances' / 'instances.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=999')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='Unsupported'):
        store(tmp_path)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_reorder_add_edit_and_two_conversations_reuse_only_exact_members(tmp_path, endpoint, scopes):
    f, p = factory(tmp_path, endpoint, scopes())
    second = {**RECIPE, 'name': 'Second'}
    try:
        a = (await f({'member': RECIPE}, conversation_id='chat'))[0]
        pair = await f({'other': second, 'member': RECIPE}, conversation_id='chat')
        reverse = await f({'member': RECIPE, 'other': second}, conversation_id='chat')
        assert pair == list(reversed(reverse)) and reverse[0] is a
        assert len(p.calls) == 2
        edited = await f({'member': {**RECIPE, 'instructions': 'edited'}, 'other': second}, conversation_id='chat')
        assert edited[0] is not a and edited[0].id == a.id and edited[1] is pair[0]
        assert a.instructions != edited[0].instructions
        assert len(p.calls) == 3
        b = (await f({'member': RECIPE}, conversation_id='another-chat'))[0]
        assert b.id != a.id
        assert (await a.call_tool('shell__execute', {'command': 'pwd'}))['session'] == 'session-a'
        assert (await b.call_tool('shell__execute', {'command': 'pwd'}))['session'] == 'session-a'
        # Fixture grants alternate by owner; isolation in production is enforced
        # by the gateway, not inferred here from the returned session label.
        assert len({intent.instance_id for intent in p.calls}) == 3
        assert f.bindings_for(a).toolsets['shell'] is a.providers['shell']
        with pytest.raises(ValueError):
            f.bindings_for(SimpleNamespace(id=a.id, _instance_identity=a._instance_identity))
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_lost_reply_and_reopened_composition_keep_operation_identity(tmp_path, endpoint, scopes):
    f, p = factory(tmp_path, endpoint, scopes())
    p.fail_once = True
    with pytest.raises(TimeoutError):
        await f({'member': RECIPE}, conversation_id='new-chat')
    first = p.calls[0]
    await f.shutdown()
    f = ProvisionedAgentInstanceFactory(store(tmp_path), p, model_scope=scopes())
    try:
        agent = (await f({'member': RECIPE}, conversation_id='new-chat'))[0]
        assert p.calls[1] == first and str(agent.id) == first.instance_id
        assert (await agent.call_tool('shell__execute', {'command': 'resume'}))['session'] == 'session-a'
        assert len(p.operations) == 1
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_cancelled_observer_does_not_cancel_allocation_or_duplicate_clients(tmp_path, endpoint, scopes):
    f, p = factory(tmp_path, endpoint, scopes())
    p.release.clear()
    configs = {'member': copy.deepcopy(RECIPE)}
    one = asyncio.create_task(f(configs, conversation_id='chat'))
    await p.entered.wait()
    two = asyncio.create_task(f({'member': RECIPE}, conversation_id='chat'))
    configs['member']['instructions'] = 'later mutation'
    one.cancel()
    with pytest.raises(asyncio.CancelledError):
        await one
    p.release.set()
    try:
        agent = (await two)[0]
        assert len(p.calls) == 1 and agent.instructions != 'later mutation'
        assert (await f({'member': RECIPE}, conversation_id='chat'))[0] is agent
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_shutdown_joins_pending_delivery_and_inflight_tool_calls(tmp_path, endpoint, scopes):
    f, p = factory(tmp_path, endpoint, scopes())
    a = (await f({'member': RECIPE}, conversation_id='chat'))[0]
    endpoint.hold = True
    call = asyncio.create_task(a.call_tool('shell__execute', {'command': 'write'}))
    assert await asyncio.to_thread(endpoint.entered.wait, 2)
    p.entered.clear()
    p.release.clear()
    allocation = asyncio.create_task(f({'member': RECIPE}, conversation_id='another'))
    await p.entered.wait()
    closing = asyncio.create_task(f.shutdown())
    await asyncio.sleep(.01)
    closing.cancel()
    p.release.set()
    with pytest.raises(RuntimeError, match='stopping'):
        await allocation
    await asyncio.sleep(.01)
    assert not closing.done()
    with pytest.raises(RuntimeError, match='stopping'):
        await f({'member': RECIPE}, conversation_id='late')
    endpoint.release.set()
    await call
    with pytest.raises(asyncio.CancelledError):
        await closing
    await f.shutdown()
    for binding in p.delivered:
        with pytest.raises(RuntimeError, match='closed'):
            await binding.tools.toolsets['shell'].call_tool('execute', {'command': 'late'})
    store(tmp_path).close()


@pytest.mark.asyncio
async def test_bad_binding_closes_new_clients_without_closing_borrowed_one(tmp_path, endpoint, scopes):
    f, p = factory(tmp_path, endpoint, scopes())
    a = (await f({'member': RECIPE}, conversation_id='chat'))[0]
    borrowed = a.providers['shell']
    fresh = provider(endpoint, 'files')
    async def bad(intent):
        return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings({'shell': borrowed, 'files': fresh}))
    p.bind = bad
    try:
        with pytest.raises(ValueError, match='reused'):
            await f({'member': RECIPE}, conversation_id='other')
        with pytest.raises(RuntimeError, match='closed'):
            await fresh.call_tool('execute', {'command': 'closed'})
        assert (await borrowed.call_tool('execute', {'command': 'alive'}))['session'] == 'session-a'
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_actual_runtime_creates_and_reopens_previously_unknown_conversations(tmp_path, endpoint, scopes):
    scope = scopes()
    f, p = factory(tmp_path, endpoint, scope)
    template = TeamConfig(id='template', name='Test', description='', icon='')
    templates = SimpleNamespace(prepare_team=lambda _: ({'member': RECIPE}, ['shell'], []),
        get_template=lambda _: template, dict_to_team_config=TeamConfig.from_dict)
    def runtime(factory):
        env = AgentEnvironment(
            projects=SimpleNamespace(list_projects=lambda: [], active_project=None, default_project=None),
            templates=templates, settings=lambda: scope.settings,
            ensure_services=AsyncMock(), create_agents=factory, validate_model=lambda _: (True, ''),
            create_plugins=AsyncMock(return_value=[]), close_agents=factory.shutdown)
        return AgentRuntime(memory_dir=str(scope.settings.pantheon_dir / 'conversations'), environment=env)
    app = runtime(f)
    identities, chats = [], []
    try:
        await app.run_setup()
        for name, session in (('new-chat', 'session-a'), ('next-chat', 'session-b')):
            created = await app.create_chat(name)
            assert created['success']
            chat = created['chat_id']
            chats.append(chat)
            info = await app.get_agents(chat)
            assert info['success']
            identities.append(info['agents'][0]['instance']['instance_id'])
            a = app.chat_teams[chat].team_agents[0]
            assert (await a.call_tool('shell__execute', {'command': 'pwd'}))['session'] == session
            assert 'a' * 64 not in json.dumps(info)
        assert len(set(identities)) == 2
    finally:
        await app.cleanup()
    first_intents = tuple(p.calls)
    f = ProvisionedAgentInstanceFactory(store(tmp_path), p, model_scope=scope)
    restored = runtime(f)
    try:
        await restored.run_setup()
        listing = await restored.list_chats()
        assert {chat['id'] for chat in listing['chats']} == set(chats)
        for chat, identity in zip(chats, identities):
            info = await restored.get_agents(chat)
            assert info['success'] and info['agents'][0]['instance']['instance_id'] == identity
        assert tuple(p.calls[2:]) == first_intents
    finally:
        await restored.cleanup()
    store(tmp_path).close()


@pytest.mark.asyncio
async def test_partial_team_failure_retries_only_failed_member(tmp_path, endpoint, scopes):
    f, p = factory(tmp_path, endpoint, scopes())
    original = p.bind
    failed = False
    async def intermittent(intent):
        nonlocal failed
        if intent.config_id == 'second' and not failed:
            failed = True
            raise TimeoutError('second resource unavailable')
        return await original(intent)
    p.bind = intermittent
    configs = {'first': RECIPE, 'second': {**RECIPE, 'name': 'Other'}}
    try:
        with pytest.raises(TimeoutError):
            await f(configs, conversation_id='chat')
        original_provider = p.delivered[0].tools.toolsets['shell']
        pair = await f(configs, conversation_id='chat')
        assert pair[0].providers['shell'] is original_provider
        assert [intent.config_id for intent in p.calls] == ['first', 'second']
        assert (await pair[0].call_tool('shell__execute', {'command': 'alive'}))['session'] == 'session-a'
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_wrong_identity_cleanup_failure_blocks_new_allocation_until_shutdown(tmp_path, endpoint, scopes):
    from dataclasses import replace
    f, p = factory(tmp_path, endpoint, scopes())
    original = p.bind
    cleanup_attempts = []
    async def invalid(intent):
        binding = await original(intent)
        client = binding.tools.toolsets['shell']
        shutdown = client.shutdown
        async def fail_once():
            cleanup_attempts.append(True)
            if len(cleanup_attempts) == 1:
                raise RuntimeError('transport close failed')
            await shutdown()
        client.shutdown = fail_once
        return replace(binding, conversation_id='wrong-conversation')
    p.bind = invalid
    with pytest.raises(RuntimeError, match='client cleanup'):
        await f({'member': RECIPE}, conversation_id='chat')
    with pytest.raises(RuntimeError, match='needs recovery'):
        await f({'member': RECIPE}, conversation_id='chat')
    assert len(p.calls) == 1
    await f.shutdown()
    assert len(cleanup_attempts) == 2
    with pytest.raises(RuntimeError, match='closed'):
        await p.delivered[0].tools.toolsets['shell'].call_tool('execute', {'command': 'closed'})
    store(tmp_path).close()


@pytest.mark.asyncio
async def test_shutdown_cannot_detach_sqlite_reservation(tmp_path, endpoint, scopes, monkeypatch):
    from threading import Event
    f, p = factory(tmp_path, endpoint, scopes())
    entered, release = Event(), Event()
    original = f.store.reserve
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(f.store, 'reserve', slow)
    request = asyncio.create_task(f({'member': RECIPE}, conversation_id='chat'))
    assert await asyncio.to_thread(entered.wait, 2)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    closing = asyncio.create_task(f.shutdown())
    try:
        await asyncio.sleep(.01)
        assert not closing.done() and not p.calls
        release.set()
        await closing
        assert not p.calls  # No resource allocation after stopping.
        reopened = store(tmp_path)
        try:
            intent = reopened.reserve('chat', {'member': RECIPE})[0]
            with sqlite3.connect(tmp_path / 'instances' / 'instances.sqlite3') as db:
                assert db.execute('SELECT operation_id FROM revisions').fetchall() == [(intent.operation_id,)]
        finally:
            reopened.close()
    finally:
        release.set()
        await f.shutdown()
