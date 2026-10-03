"""Agent backend entry for the ordinary Fleet HTTP App host.

No ambient bus, platform service or legacy ChatRoom is constructed. The prepared
snapshot supplies models and scoped allocation; App data owns event replay.
"""
from copy import deepcopy
from dataclasses import asdict

from pantheon.apps.toolset_backend import register_toolset
from pantheon.chatroom.event_store import AgentEventStore
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.toolset import tool
from pantheon.internal.memory.memory import _ALL_CONTEXTS
from pantheon.utils.misc import run_func


class NativeAgentApplication(ConfiguredAgentApplication):
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
        return {'protocol': 1, 'history_protocol': 1, 'event_protocol': 1}

    async def run_setup(self):
        if self._nats_adapter is not None:
            raise ValueError('Native Agent events must use the App-owned replay transport')
        self._nats_adapter = AgentEventStore(self.app_data.root / 'events')
        await self._nats_adapter.recover_interrupted_streams()
        await super().run_setup()

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
