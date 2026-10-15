"""The `pantheon` command, run by an agent (docs/pantheon-cli.md).

A command the agent runs gets three variables: the Agent App's own RPC
endpoint, a grant that allows only its pantheon_command method there, and a
token for this conversation, this agent and this tool call. The command sends
each request to pantheon_command; the Agent App carries it out as that agent,
with the agent's own grants and `apps` filter, and shows the App calls it
made under the command in the conversation. The sandbox gets no other
authority.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pantheon.utils.log import logger

# A token outlives its command a while: a command may start background work
# that calls pantheon later.
TOKEN_SECONDS = 6 * 3600


@dataclass
class LiveCall:
    """A run_command in progress: where its App calls are shown."""
    agent: Any
    chat_id: str
    tool_call_id: str
    run_context: Any
    context_variables: dict = field(default_factory=dict)
    interrupt: bool = False
    shown: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class AgentRun:
    """A sub-agent a command started (`pantheon agent run`)."""
    task: asyncio.Task
    chat_id: str
    agent: str
    owner_call: str
    detached: bool
    started: float = field(default_factory=time.time)


#: The longest a single `agent.wait` request holds (the command asks again).
AGENT_WAIT_SECONDS = 240


class CommandGateway:
    def __init__(self, grant: Callable[[], Any], team_for_chat: Callable):
        # grant() -> the allocated provider of this App's pantheon_command.
        self._grant = grant
        self._team_for_chat = team_for_chat
        self._secret = secrets.token_bytes(32)
        self._live: dict[str, LiveCall] = {}
        self._runs: dict[str, AgentRun] = {}

    # ---- tokens ----

    def _sign(self, body: bytes) -> str:
        return base64.urlsafe_b64encode(hmac.new(self._secret, body, hashlib.sha256).digest()).decode().rstrip('=')

    def mint(self, *, chat_id: str, agent: str, tool_call_id: str) -> str:
        claims = {'chat': chat_id, 'agent': agent, 'call': tool_call_id, 'exp': int(time.time()) + TOKEN_SECONDS}
        body = base64.urlsafe_b64encode(json.dumps(claims, separators=(',', ':')).encode()).decode().rstrip('=')
        return body + '.' + self._sign(body.encode())

    def verify(self, token: str) -> dict:
        try:
            body, signature = token.split('.')
            if not hmac.compare_digest(signature, self._sign(body.encode())):
                raise ValueError
            claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
            if not isinstance(claims, dict) or claims.get('exp', 0) < time.time():
                raise ValueError
            return claims
        except (ValueError, TypeError, AttributeError):
            raise PermissionError('This command token is not valid (expired, or from an earlier Agent run)') from None

    # ---- the agent's side ----

    async def open(self, agent, context_variables: dict | None, tool_call_id: str | None) -> dict | None:
        """The variables for one run_command, or None when commands cannot
        reach this Agent (no grant in this deployment, or no conversation)."""
        from pantheon.agent import get_current_run_context
        chat_id = (context_variables or {}).get('chat_id') or (context_variables or {}).get('client_id')
        if not chat_id or not tool_call_id:
            return None
        try:
            provider = self._grant()
            if provider is None:
                return None
            bound = await provider._current()
            credential = bound._client.credential
        except Exception as exc:
            logger.warning(f'pantheon commands are unavailable for this run: {exc}')
            # The command says why instead of asking to sign in.
            return {'PANTHEON_UNAVAILABLE': f'The Agent could not give this command access: {exc}'[:500]}
        self._live[tool_call_id] = LiveCall(agent, chat_id, tool_call_id, get_current_run_context(),
                                            dict(context_variables or {}))
        return {'PANTHEON_ENDPOINT': credential.endpoint, 'PANTHEON_GRANT': credential.key,
                'PANTHEON_TOKEN': self.mint(chat_id=chat_id, agent=agent.name, tool_call_id=tool_call_id)}

    def close(self, tool_call_id: str | None) -> bool:
        """End a run_command; True when the command asked to stop the run.

        A sub-agent the command started and still waits for ends with it (the
        run was stopped); one started with --detach keeps running."""
        live = self._live.pop(tool_call_id or '', None)
        for run in self._runs.values():
            if run.owner_call == tool_call_id and not run.detached and not run.task.done():
                run.task.cancel()
        return bool(live and live.interrupt)

    # ---- the command's side ----

    async def handle(self, token: str, request: dict) -> dict:
        claims = self.verify(token)
        if not isinstance(request, dict) or not isinstance(request.get('op'), str):
            raise ValueError('Supply {op, params}')
        params = request.get('params') or {}
        if not isinstance(params, dict):
            raise ValueError('params is an object')
        live = self._live.get(claims['call'])
        agent = live.agent if live else await self._agent(claims)
        if request['op'].startswith('agent.'):
            return await self._agent_op(claims, agent, live, request['op'], params)
        result, shown = await execute(agent, request['op'], params, live)
        if live and shown is not None:
            await self._show(live, *shown, result)
        if isinstance(result, dict) and isinstance(result.get('content_blocks'), list):
            # A command prints text: images stay out of its output.
            result = {k: v for k, v in result.items() if k != 'content_blocks'}
            result['images'] = 'not shown in a command; use file_manager observe_images on the image files'
        return result

    # ---- sub-agents ----

    async def _agent_op(self, claims: dict, agent, live: LiveCall | None, op: str, params: dict) -> dict:
        if op in ('agent.list', 'agent.start'):
            team = await self._team_for_chat(claims['chat']) if self._team_for_chat else None
            available = profiles(team, agent)
            if op == 'agent.list':
                return {'profiles': available}
            return self._start(claims, agent, live, params, available)
        run = self._runs.get(str(params.get('run_id') or ''))
        if run is None or run.chat_id != claims['chat']:
            raise ValueError('No such sub-agent run in this conversation')
        if op == 'agent.wait':
            timeout = min(max(float(params.get('timeout') or AGENT_WAIT_SECONDS), 1), AGENT_WAIT_SECONDS)
            await asyncio.wait([run.task], timeout=timeout)
            return self._outcome(params['run_id'], run)
        if op == 'agent.stop':
            run.task.cancel()
            await asyncio.wait([run.task], timeout=10)
            return self._outcome(params['run_id'], run)
        raise ValueError(f'Unknown request {op!r}')

    def _start(self, claims: dict, agent, live: LiveCall | None, params: dict, available: list[dict]) -> dict:
        """Start a sub-agent as the leader's call_agent does, attached to the
        command: its steps are child messages of the run_command call."""
        from pantheon.agent import _RUN_CONTEXT
        import contextvars
        if live is None:
            raise PermissionError('A sub-agent is started by a running command of its conversation')
        if 'call_agent' not in getattr(agent, '_base_functions', {}):
            raise PermissionError(f'{agent.name} does not start sub-agents; only the conversation\'s agent does')
        profile, task = str(params.get('profile') or ''), str(params.get('task') or '')
        if not profile or not task.strip():
            raise ValueError('Give a profile (pantheon agent list) and a task')
        names = [p['name'] for p in available]
        if profile not in names:
            raise ValueError(f'Unknown profile {profile!r}; profiles: {", ".join(names) or "none"}')
        context_variables = {**live.context_variables, '_command_parent': True}
        # Runs in the leader's run context: its memory, model and step relay.
        context = contextvars.copy_context()
        context.run(_RUN_CONTEXT.set, live.run_context)
        coroutine = agent.call_tool('call_agent', {'agent_name': profile, 'instruction': task},
                                    context_variables, live.tool_call_id)
        run_id = 'agent_' + secrets.token_hex(4)
        self._runs[run_id] = AgentRun(asyncio.get_running_loop().create_task(coroutine, context=context),
                                      claims['chat'], profile, live.tool_call_id, bool(params.get('detach')))
        self._forget_old_runs()
        return {'run_id': run_id, 'profile': profile}

    @staticmethod
    def _outcome(run_id: str, run: AgentRun) -> dict:
        if not run.task.done():
            return {'run_id': run_id, 'done': False}
        if run.task.cancelled():
            return {'run_id': run_id, 'done': True, 'error': 'The sub-agent was stopped'}
        exc = run.task.exception()
        if exc is not None:
            return {'run_id': run_id, 'done': True, 'error': f'{type(exc).__name__}: {exc}'[:2000]}
        answer = run.task.result()
        return {'run_id': run_id, 'done': True, 'answer': answer if isinstance(answer, str) else
                json.dumps(answer, ensure_ascii=False, default=str)}

    def _forget_old_runs(self) -> None:
        cutoff = time.time() - TOKEN_SECONDS
        for run_id in [r for r, run in self._runs.items() if run.task.done() and run.started < cutoff]:
            self._runs.pop(run_id, None)

    async def _agent(self, claims):
        team = await self._team_for_chat(claims['chat'])
        for agent in members(team):
            if agent.name == claims['agent']:
                return agent
        raise PermissionError('The agent of this command is no longer in its conversation')

    async def _show(self, live: LiveCall, name: str, args: dict, result: Any) -> None:
        """Record an App call the command made, under the command."""
        hook = getattr(live.run_context, 'process_step_message', None)
        if hook is None:
            return
        from pantheon.utils.misc import run_func
        async with live.lock:
            live.shown += 1
            call_id = f'{live.tool_call_id}:{live.shown}'
        context = f'{live.chat_id}|cli|{live.tool_call_id}'
        common = {'execution_context_id': context, 'parent_tool_call_id': live.tool_call_id,
                  '_metadata': {'chain_path': [live.agent.name, f'pantheon:{live.tool_call_id}']}}
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        try:
            await run_func(hook, {'role': 'assistant', 'content': '', 'id': call_id + ':call', **common,
                                  'tool_calls': [{'id': call_id, 'type': 'function', 'function': {
                                      'name': name, 'arguments': json.dumps(args, ensure_ascii=False, default=str)}}]})
            await run_func(hook, {'role': 'tool', 'tool_call_id': call_id, 'tool_name': name, 'name': name,
                                  'id': call_id + ':result', 'content': content[:200_000], **common})
        except Exception as exc:
            logger.debug(f'pantheon command call not shown: {exc}')


def profiles(team, agent) -> list[dict]:
    """The sub-agent profiles an agent can start: its team's other agents."""
    others = [a for a in members(team) if a is not agent] if team is not None else []
    return [{'name': a.name, 'description': getattr(a, 'description', '') or ''} for a in others]


def members(team) -> list:
    """Every agent of a team: its members and the profiles they start."""
    agents = getattr(team, 'agents', None)
    if isinstance(agents, dict):
        return list(agents.values())
    return list(agents or getattr(team, 'team_agents', None) or [])


async def execute(agent, op: str, params: dict, live: LiveCall | None):
    """Carry out one request as the agent. Returns (result, shown), where
    shown is the App call to show under the command (name, args) or None."""
    from pantheon.agent_pantheonos import PantheonOS
    pos = PantheonOS(agent)
    p = params

    async def tool(name, args):
        return await agent.call_tool(name, args), (name, args)

    if op == 'apps.list':
        return await pos.pantheonos_list_apps(p.get('query', '')), None
    if op == 'apps.describe':
        return await pos.pantheonos_describe_app(p['app']), None
    if op == 'apps.call':
        app, name, args = p['app'], p['tool'], p.get('args') or {}
        result = await pos.pantheonos_call_app(app, name, args)
        return result, (f'{app}__{name}', args)
    if op == 'apps.start':
        return await pos.pantheonos_start_app(p['app'], p.get('node', ''), p.get('version', ''),
                                              bool(p.get('window')), p.get('job')), None
    if op == 'apps.stop':
        return await pos.pantheonos_stop_app(p['app'], p.get('node', '')), None
    if op == 'apps.manage':
        return await pos.pantheonos_manage_app(p['action'], p.get('app', ''), p.get('args') or {}), None
    if op == 'dev':
        return await pos.pantheonos_develop_app(p['action'], p.get('app', ''), p.get('args') or {}), None
    if op in ('desktop', 'browser'):
        return await tool(f"desktop__{op}_{p['action']}", p.get('args') or {})
    if op == 'nodes.list':
        return await tool('fleet__fleet_list_nodes', {})
    if op == 'nodes.info':
        return await tool('fleet__fleet_node_info', {'node_id': p['node']})
    if op == 'run':
        return await tool('fleet__run_on_node', {k: p[k] for k in ('node_id', 'code', 'kind', 'timeout') if k in p})
    if op == 'transfer':
        return await tool('fleet__transfer', {k: p[k] for k in ('src_node', 'src_path', 'dst_node', 'dst_path',
                                                                'wait', 'timeout') if k in p})
    if op == 'task.boundary':
        return await tool('task__task_boundary', p)
    if op == 'task.output':
        return await tool('task__register_output', p)
    if op == 'task.outputs':
        return await agent.call_tool('task__list_outputs', {}), None
    if op == 'skills.view':
        args = {'name': p['name']}
        if p.get('file'):
            args['file_path'] = p['file']
        return await agent.call_tool('skills__skill_view', args), None
    if op == 'skills.manage':
        keep = ('action', 'name', 'content', 'old_string', 'new_string', 'replace_all', 'options')
        return await agent.call_tool('skills__skill_manage', {k: p[k] for k in keep if k in p}), None
    if op == 'jobs':
        keep = ('action', 'task_id', 'timeout')
        return await agent.call_tool('background_task', {k: p[k] for k in keep if k in p}), None
    if op == 'task.notify':
        result = await agent.call_tool('task__notify_user', p)
        if live and isinstance(result, dict) and result.get('interrupt'):
            live.interrupt = True
        return result, ('task__notify_user', p)
    raise ValueError(f'Unknown request {op!r}')
