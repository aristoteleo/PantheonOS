"""Agent backend entry for the ordinary Fleet HTTP App host.

No ambient bus, platform service or legacy ChatRoom is constructed. The prepared
snapshot supplies models and scoped allocation; App data owns event replay.
"""
from copy import deepcopy
from dataclasses import asdict

from pantheon.apps.toolset_backend import register_toolset
from pantheon.chatroom.event_store import AgentEventStore
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.chatroom.settings_document import AgentSettingsDocument
from pantheon.chatroom.skill_files import AgentSkillFiles
from pantheon.toolset import tool
from pantheon.internal.memory.memory import _ALL_CONTEXTS
from pantheon.utils.misc import run_func
from pantheon.utils.owned_io import run_owned_io


class NativeAgentApplication(ConfiguredAgentApplication):
    @tool(exclude=True)
    async def agent_skill_files(self, operation: str, scope: str, path: str = '',
                                content: str | None = None, revision: str | None = None,
                                offset: int = 0, target_scope: str | None = None,
                                overwrite: bool = False) -> dict:
        """Author App-private skills with relative paths and bounded reads."""
        return await run_owned_io(self._skill_files.call, operation, scope, path,
                                 content=content, revision=revision, offset=offset,
                                 target_scope=target_scope, overwrite=overwrite)

    @tool(exclude=True)
    async def get_agent_settings(self) -> dict:
        """Read private runtime preferences, excluding credentials and grants."""
        return await run_owned_io(self._settings_document.read)

    @tool(exclude=True)
    async def save_agent_settings(self, expected_revision: str, overrides: dict) -> dict:
        """Save a checked revision for next restart, without changing active Runs."""
        return await run_owned_io(self._settings_document.save, expected_revision, overrides)

    @tool(exclude=True)
    async def get_active_project(self) -> dict:
        """Display this App's attached workspace, without global project discovery.

        These paths are metadata, not filesystem capabilities. File access still
        requires an explicitly bound Files service and its workspace grant.
        """
        projects = self.app_data.projects
        return {'success': True,
                'active': asdict(projects.active_project) if projects.active_project else None,
                'home': asdict(projects.default_project) if projects.default_project else None}

    @tool(exclude=True)
    async def get_agent_app_info(self) -> dict:
        """Negotiate the native GUI contract over this instance's App RPC.

        The portable host exposes methods only after setup has completed. This
        is not the legacy bus ping and does not discover another Agent service.
        """
        return {'protocol': 1, 'history_protocol': 1, 'event_protocol': 1, 'event_cursor_protocol': 1,
                'execution_protocol': 1}

    async def run_setup(self):
        if self._nats_adapter is not None:
            raise ValueError('Native Agent events must use the App-owned replay transport')
        self._settings_document = AgentSettingsDocument(self.app_models.settings)
        self._skill_files = AgentSkillFiles(self.app_models.settings)
        self._nats_adapter = AgentEventStore(self.app_data.root / 'events')
        await self._nats_adapter.recover_interrupted_streams()
        await super().run_setup()
        from pantheon.chatroom.execution_engine import AgentExecutionEngine
        from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
        journal = await run_owned_io(ExecutionJournal, self.app_data.root / 'executions')
        self._executions = AgentExecutions(journal, AgentExecutionEngine(self.app_models.scope,
            validate_model=self.app_models.validate, refresh_models=self.app_models.refresh))

    async def begin_shutdown(self):
        await super().begin_shutdown()
        if executions := getattr(self, '_executions', None):
            await executions.close()

    @tool(exclude=True)
    async def agent_execution_submit(self, consumer_id: str, execution_id: str, specification: dict) -> dict:
        """Submit one idempotent multi-turn execution. Bind consumer_id in its grant."""
        return await self._executions.submit(consumer_id, execution_id, specification)

    @tool(exclude=True)
    async def agent_execution_poll(self, consumer_id: str, execution_id: str) -> dict:
        """Observe status and a tool request; observation alone never claims a tool."""
        return await self._executions.poll(consumer_id, execution_id)

    @tool(exclude=True)
    async def agent_execution_claim(self, consumer_id: str, execution_id: str,
                                    call_id: str, worker_id: str) -> dict:
        """Claim tool effects before executing them using the caller's durable ledger."""
        return await self._executions.claim(consumer_id, execution_id, call_id, worker_id)

    @tool(exclude=True)
    async def agent_execution_reply(self, consumer_id: str, execution_id: str,
                                    call_id: str, worker_id: str, response: dict) -> dict:
        """Acknowledge a claimed tool outcome; identical replies are idempotent."""
        return await self._executions.reply(consumer_id, execution_id, call_id, worker_id, response)

    @tool(exclude=True)
    async def agent_execution_cancel(self, consumer_id: str, execution_id: str) -> dict:
        """Stop inference; pending_tools still belong to the caller and need settling."""
        return await self._executions.cancel(consumer_id, execution_id)

    @tool(exclude=True)
    async def agent_execution_read_result(self, consumer_id: str, execution_id: str,
                                          offset: int = 0) -> dict:
        """Read bounded base64 result bytes and verify the complete SHA-256 digest."""
        return await self._executions.read_result(consumer_id, execution_id, offset)

    @tool(exclude=True)
    async def agent_execution_release(self, consumer_id: str, execution_id: str) -> dict:
        """Remove a settled execution's result while retaining its deduplication receipt."""
        return await self._executions.release(consumer_id, execution_id)

    @tool(exclude=True)
    async def get_agent_event_cursor(self, chat_id: str) -> dict:
        """Anchor observation before a new turn without copying saved history.

        This cursor does not restore earlier messages or active stream prefixes.
        Reconnecting clients must still use open_agent_history. Cursor capture
        neither submits nor reserves a turn; queued admission remains explicit.
        """
        if not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 256:
            raise ValueError('Supply an Agent conversation identity')
        return {'protocol': 1, 'chat_id': chat_id, 'cursor': await self._nats_adapter.position()}

    @tool(exclude=True)
    async def read_agent_events(self, chat_id: str, cursor: dict | None = None, limit: int = 128) -> dict:
        """Read ordered JSON fragments; on reset_required refresh saved chat history.

        Reassemble all parts of one event_id before parsing its JSON. Persist the
        returned cursor after applying the page. No events from other chats are
        returned. An unfinished fragment group must also survive paging/retries.
        """
        return await self._nats_adapter.read(chat_id, cursor, limit)

    @tool(exclude=True)
    async def open_agent_history(self, chat_id: str) -> dict:
        """Snapshot full history without presentation truncation.

        Read all parts, verify size/digest, then parse JSON. Resume events from
        cursor and reconcile message identities; it deliberately precedes the
        copy. On reset_required discard partial replay and take a new snapshot.
        Release the snapshot when consumed. Unreleased snapshots expire in 10 min.
        """
        cursor, inflight = await self._nats_adapter.history_checkpoint(chat_id)
        memory = await run_func(self.memory_manager.get_memory, chat_id)
        # get_messages(False) returns shared dictionaries. Detach on the Agent
        # loop, without yielding between enumeration and copy.
        messages = deepcopy(memory.get_messages(_ALL_CONTEXTS, False) or [])
        complete = {message.get('id') for message in messages if isinstance(message.get('id'), str)}
        inflight = [event for event in inflight if (
            event['data'].get('chunk', {}) if event['type'] == 'chunk' else event['data']
        ).get('message_id') not in complete]
        # A replay gap may have removed the terminal event. Do not preserve a
        # stale GUI spinner after restoring an already finished conversation.
        running = chat_id in self.threads or self._chat_has_running_bg_tasks(chat_id)
        return await self._nats_adapter.save_history(chat_id, messages, cursor, inflight, running=running)

    @tool(exclude=True)
    async def read_agent_history(self, chat_id: str, snapshot_id: str, part: int) -> dict:
        """Read a stable JSON fragment (bounded for the Fleet RPC envelope)."""
        return await self._nats_adapter.read_history(chat_id, snapshot_id, part)

    @tool(exclude=True)
    async def release_agent_history(self, chat_id: str, snapshot_id: str) -> dict:
        """Idempotently release an already downloaded history snapshot."""
        return await self._nats_adapter.release_history(chat_id, snapshot_id)


async def register(ctx):
    ctx.require_rpc_token = True
    data = ctx.state_dir / 'agent'
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    app = NativeAgentApplication(ctx.app_id, data_dir=data, allow_in_place_restart=False)
    await register_toolset(ctx, app)
