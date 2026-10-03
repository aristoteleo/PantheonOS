"""Agent backend entry for the ordinary Fleet HTTP App host.

No ambient bus, platform service or legacy ChatRoom is constructed. The prepared
snapshot supplies models and scoped allocation; App data owns event replay.
"""
from pantheon.apps.toolset_backend import register_toolset
from pantheon.chatroom.event_store import AgentEventStore
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.toolset import tool


class NativeAgentApplication(ConfiguredAgentApplication):
    async def run_setup(self):
        if self._nats_adapter is not None:
            raise ValueError('Native Agent events must use the App-owned replay transport')
        self._nats_adapter = AgentEventStore(self.app_data.root / 'events')
        await super().run_setup()

    @tool(exclude=True)
    async def read_agent_events(self, chat_id: str, cursor: dict | None = None, limit: int = 128) -> dict:
        """Read ordered JSON fragments; on reset_required refresh saved chat history.

        Reassemble all parts of one event_id before parsing its JSON. Persist the
        returned cursor after applying the page. No events from other chats are
        returned. An unfinished fragment group must also survive paging/retries.
        """
        return await self._nats_adapter.read(chat_id, cursor, limit)


async def register(ctx):
    ctx.require_rpc_token = True
    data = ctx.state_dir / 'agent'
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    app = NativeAgentApplication(ctx.app_id, data_dir=data, allow_in_place_restart=False)
    await register_toolset(ctx, app)
