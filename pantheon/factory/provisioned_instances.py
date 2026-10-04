"""Dynamic Agent creation using durable intents and explicit resource delivery.

The supplied provisioner is a composition capability, never template code. Its
bind(intent) operation must recover the same operation/session after a lost
reply; it must not silently replace a missing resource. The App does not issue
grants. Platform-side provisioning and renewal remain the provisioner's job.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from pantheon.dependency_provider import _drain_call
from pantheon.factory.bindings import _thaw
from pantheon.factory.instances import AgentInstanceBinding, AgentInstanceFactory, _config, _identifier
from pantheon.factory.instance_store import AgentInstanceStore, InstanceIntent
from pantheon.utils.model_scope import ModelCallScope


class InstanceProvisioner(Protocol):
    """Trusted, bounded allocation using exact-owner capabilities.

    operation_id identifies revision assembly, not a new logical session owner.
    Compatible owned resources (e.g. Shell state) stay keyed to instance_id across
    configuration edits; reconfiguration must not implicitly reset that state.
    The provisioner owns undelivered/partially acquired resources on exceptions.
    Returned clients transfer to the factory; shared services need fresh clients.
    """
    async def bind(self, intent: InstanceIntent) -> AgentInstanceBinding: ...

    async def retire(self, instance_id: str) -> dict: ...


class ProvisionedAgentInstanceFactory:
    """App-owned dynamic factory compatible with AgentEnvironment.create_agents.

    A new revision produces new Agent/client objects with the same durable
    instance ID, leaving older Run objects untouched until the App drains them.
    Shutdown closes clients and the local store; it does not assert that remote
    sessions were released. Explicit instance retirement is a separate operation.
    """
    def __init__(self, store: AgentInstanceStore, provisioner: InstanceProvisioner, *, model_scope):
        if not isinstance(store, AgentInstanceStore) or not callable(getattr(provisioner, "bind", None)):
            raise ValueError("Dynamic instances require a store and explicit provisioner")
        if not isinstance(model_scope, ModelCallScope):
            raise ValueError("Dynamic instances require an explicit model scope")
        self.store, self.provisioner, self.model_scope = store, provisioner, model_scope
        self._tasks, self._factories = {}, {}
        self._requests = set()
        self._request_chats = {}
        self._retiring = set(store.retirements())
        self._retire_tasks = {}
        self._provider_owners = {}
        self._cleanup_failed = False
        self._closed = False
        self._closing = None

    async def __call__(self, agent_configs, *, conversation_id=None):
        self._check_open()
        if (not _identifier(conversation_id) or not isinstance(agent_configs, dict)
                or not 1 <= len(agent_configs) <= 256 or not all(_identifier(key) for key in agent_configs)):
            raise ValueError("Supply a conversation and its member configurations")
        if conversation_id in self._retiring:
            raise ValueError('Conversation is retiring or retired')
        prepared = {key: _config(value) for key, value in agent_configs.items()}
        if len({value[0]["name"] for value in prepared.values()}) != len(prepared):
            raise ValueError("Conversation member names must be distinct")
        # Each admitted request owns its SQLite work even if its observer leaves.
        # The durable per-member key below coalesces overlapping team requests.
        configs = {name: value[0] for name, value in prepared.items()}
        task = asyncio.create_task(self._resolve(conversation_id, configs))
        self._requests.add(task)
        self._request_chats[task] = conversation_id
        task.add_done_callback(self._request_done)
        return await asyncio.shield(task)

    def _request_done(self, task):
        self._requests.discard(task)
        self._request_chats.pop(task, None)
        if not task.cancelled():
            task.exception()

    def _check_open(self):
        if self._closed:
            raise RuntimeError("Agent instance factory is stopping")
        if self._cleanup_failed:
            raise RuntimeError("Agent instance factory needs recovery after failed client cleanup")

    async def _resolve(self, conversation_id, configs):
        intents = await asyncio.to_thread(self.store.reserve, conversation_id, configs)
        self._check_open()
        tasks = []
        for intent in intents:
            key = (intent.instance_id, intent.config_revision)
            task = self._tasks.get(key)
            if task is None or task.done() and (task.cancelled() or task.exception() is not None):
                task = self._tasks[key] = asyncio.create_task(self._assemble(key, intent))
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            tasks.append(task)
        # A failed member must not detach still-allocating siblings. Successful
        # members remain reusable on retry, without reacquiring their sessions.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._check_open()
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    async def _assemble(self, key, intent):
        claimed = []
        try:
            self._check_open()
            binding = await self.provisioner.bind(intent)
            if not isinstance(binding, AgentInstanceBinding):
                raise ValueError("Provisioner returned an invalid Agent binding")
            providers = (*binding.tools.toolsets.values(), *binding.tools.mcp_servers.values())
            # Claim all new clients before rejecting an alias: a bad binding can
            # contain both a borrowed client and new ones that need closing.
            seen, reused = set(), False
            for provider in providers:
                identity = id(provider)
                if identity in seen or identity in self._provider_owners:
                    reused = True
                else:
                    self._provider_owners[identity] = (key, provider)
                    claimed.append(provider)
                seen.add(identity)
            if reused:
                raise ValueError("Provisioner reused another Agent binding's client")
            if binding.public_identity() != intent.identity():
                raise ValueError("Provisioner changed the reserved Agent identity or revision")
            self._check_open()
            factory = AgentInstanceFactory([binding], model_scope=self.model_scope)
            agents = await factory({intent.config_id: _thaw(intent.config)},
                                   conversation_id=intent.conversation_id)
            self._check_open()
            self._factories[key] = factory
            return agents[0]
        except BaseException:
            # Failed intents retain their operation IDs. Remote release belongs
            # to the platform; this cleanup covers only delivered client objects.
            async def cleanup():
                results = await asyncio.gather(*(p.shutdown() for p in claimed), return_exceptions=True)
                for provider, result in zip(claimed, results):
                    if not isinstance(result, BaseException):
                        self._provider_owners.pop(id(provider), None)
                if any(isinstance(value, BaseException) for value in results):
                    self._cleanup_failed = True
                    raise RuntimeError("Failed Agent assembly did not finish client cleanup")
            await _drain_call(asyncio.create_task(cleanup()))
            raise

    def bindings_for(self, agent):
        if self._closed:
            raise RuntimeError("Agent instance factory is stopping")
        for factory in self._factories.values():
            try:
                return factory.bindings_for(agent)
            except ValueError:
                pass
        raise ValueError("Agent does not belong to this instance factory")

    async def shutdown(self):
        self._closed = True
        if self._closing is None:
            self._closing = asyncio.create_task(self._shutdown())
        await _drain_call(self._closing)

    async def retire(self, conversation_id):
        """Called after the application's per-conversation admission/drain barrier."""
        self._check_open()
        if not _identifier(conversation_id):
            raise ValueError('Supply a conversation identity')
        if not callable(getattr(self.provisioner, 'retire', None)):
            raise RuntimeError('Dependency provisioner does not support retirement')
        self._retiring.add(conversation_id)
        task = self._retire_tasks.get(conversation_id)
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = self._retire_tasks[conversation_id] = asyncio.create_task(self._retire(conversation_id))
        return await _drain_call(task)

    async def _retire(self, conversation_id):
        identities = set(await asyncio.to_thread(self.store.begin_retirement, conversation_id))
        await asyncio.gather(*(task for task, chat in tuple(self._request_chats.items())
                               if chat == conversation_id), return_exceptions=True)
        tasks = [task for key, task in self._tasks.items() if key[0] in identities]
        await asyncio.gather(*tasks, return_exceptions=True)
        # Old configuration revisions can still own background tool work even
        # when the current team cache no longer references those Agent objects.
        for task in tasks:
            if task.cancelled() or task.exception() is not None:
                continue
            manager = getattr(task.result(), '_bg_manager', None)
            if manager is not None:
                while pending := [t.asyncio_task for t in manager.list_tasks()
                                  if t.asyncio_task is not None and not t.asyncio_task.done()]:
                    await asyncio.gather(*pending, return_exceptions=True)
        providers = [p for key, p in self._provider_owners.values() if key[0] in identities]
        results = await asyncio.gather(*(p.shutdown() for p in providers), return_exceptions=True)
        if any(isinstance(value, BaseException) for value in results):
            raise RuntimeError('Agent resource clients did not finish draining')
        receipts = {}
        for identity in sorted(identities):
            receipt = await self.provisioner.retire(identity)
            if not isinstance(receipt, dict) or receipt.get('state') != 'retired':
                raise RuntimeError('Agent resource retirement is still pending; retry deletion')
            receipts[identity] = receipt
        await asyncio.to_thread(self.store.finish_retirement, conversation_id)
        for provider in providers:
            self._provider_owners.pop(id(provider), None)
        for mapping in (self._factories, self._tasks):
            for key in tuple(mapping):
                if key[0] in identities:
                    del mapping[key]
        return receipts

    async def _shutdown(self):
        # No new requests can enter after _closed. Existing requests retain their
        # SQLite writes and join all child assemblies before the store is closed.
        await asyncio.gather(*tuple(self._requests), return_exceptions=True)
        await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)
        await asyncio.gather(*tuple(self._retire_tasks.values()), return_exceptions=True)
        try:
            # Includes any client whose assembly cleanup failed. Hold strong
            # references until shutdown, so a recycled Python id cannot alias it.
            providers = [value[1] for value in self._provider_owners.values()]
            results = await asyncio.gather(*(p.shutdown() for p in providers), return_exceptions=True)
            if any(isinstance(value, BaseException) for value in results):
                raise RuntimeError("Agent instance clients did not finish shutdown")
            self._provider_owners.clear()
        finally:
            await asyncio.to_thread(self.store.close)
