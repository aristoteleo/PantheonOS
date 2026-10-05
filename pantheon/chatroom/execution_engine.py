"""Ordinary Agent execution with tools supplied by its calling App.

No local tool discovery or second inference implementation: use the same Agent,
Team, compression and explicitly bound Model Services as interactive chat.
Tool effects belong to the caller, not to this service's filesystem/process.
"""
import asyncio
from copy import deepcopy

from pantheon.agent import Agent, ToolInfo, ToolProvider
from pantheon.internal.compression.plugin import CompressionPlugin
from pantheon.internal.memory import Memory
from pantheon.team.pantheon import PantheonTeam
from pantheon.utils.model_scope import ModelCallScope
from pantheon.utils.image_resources import BoundImageResolver


class AgentExecutionCleanupError(RuntimeError):
    pass


class ExecutionTools(ToolProvider):
    def __init__(self, name, functions, invoke):
        self.toolset_name = name
        self.functions = {function['name']: function for function in functions}
        self.invoke = invoke

    async def list_tools(self):
        return [ToolInfo(name=name, description=function.get('description', ''),
                         inputSchema=deepcopy(function)) for name, function in self.functions.items()]

    async def call_tool(self, name, args):
        if name not in self.functions:
            raise ValueError('Tool is absent from this execution')
        return await self.invoke(self.toolset_name, name, args)


class AgentExecutionEngine:
    def __init__(self, scope: ModelCallScope, *, validate_model, refresh_models):
        if not isinstance(scope, ModelCallScope):
            raise ValueError('Agent execution requires an explicit model scope')
        self.scope = scope
        self.validate_model = validate_model
        self.refresh_models = refresh_models

    async def __call__(self, spec, invoke):
        await self.refresh_models()
        valid, _ = self.validate_model(spec['model'])
        if not valid:
            raise ValueError('Execution model is unavailable in this Agent App')
        memory = Memory(name='app-execution')
        # The shared model scope is not permission to read another chat's image
        # store. Input/tool inline images are saved under this exact Memory ID.
        images = BoundImageResolver(image_root=self.scope.settings.pantheon_dir / 'images' / memory.id)
        agent = Agent(name='app-execution', instructions=spec['instructions'],
                      model=spec['model'], model_scope=self.scope, use_memory=True,
                      memory=memory, image_resolver=images)
        turn = 0
        async def reminders(history, context):
            nonlocal turn
            turn += 1
            matching = [item for item in spec.get('turn_messages', [])
                        if item['turn'] == turn or item['repeat'] and item['turn'] < turn]
            return [{'role': 'user', 'content': item['content']} for item in matching]
        agent._ephemeral_hooks.append(reminders)
        plugin = CompressionPlugin({'enable': True, 'threshold': .8,
                                    'preserve_recent_messages': 5}, settings=self.scope.settings)
        try:
            for name, functions in spec['tools'].items():
                await agent.toolset(ExecutionTools(name, functions, invoke))
            team = PantheonTeam(agents=[agent], plugins=[plugin])
            response = await team.run(spec['prompt'], memory=memory,
                                      max_turns=spec['max_turns'] if spec['max_turns'] is not None else float('inf'))
            # A normal finish includes tools adopted into the background.
            await self._background(agent, cancel=False)
            return response.model_dump(mode='json')
        finally:
            async def close():
                try:
                    await self._background(agent, cancel=True)
                finally:
                    await plugin.on_shutdown()
            cleanup = asyncio.create_task(close())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            try:
                cleanup.result()
            except BaseException as exc:
                raise AgentExecutionCleanupError('Execution resources need recovery') from exc
            if cancelled:
                raise asyncio.CancelledError

    @staticmethod
    async def _background(agent, *, cancel):
        while pending := [item.asyncio_task for item in agent._bg_manager.list_tasks()
                          if item.asyncio_task is not None and not item.asyncio_task.done()]:
            if cancel:
                for task in pending:
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
