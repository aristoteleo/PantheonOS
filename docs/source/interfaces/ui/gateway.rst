Messaging Gateway
=================

The Pantheon Gateway connects your agents to external messaging platforms. Once configured,
users can interact with your Pantheon agents through third-party channels — the same agents,
same memory, same toolsets.

Overview
--------

The gateway acts as a bridge between Pantheon's internal NATS messaging layer and external
chat platforms. When a user sends a message on Discord or WeChat, the gateway receives it,
routes it to the configured agent or team, waits for the response, and delivers the reply
back on the originating platform.

Each configured channel operates independently. Channels can target different agents or
teams, and each channel maintains its own conversation routing. The same user's messages
from different channels are correlated by their platform user ID and mapped into Pantheon's
conversation memory.

Supported Channels
------------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Platform
     - Support
   * - **Discord**
     - Full message support including direct messages, server channels, and slash
       commands. Bot token authentication. Markdown formatting is preserved in Discord's
       rendering.
   * - **WeChat**
     - QR-code-based session login (WeChat personal accounts). Messages are routed to
       the configured agent; responses are sent as WeChat text messages.
   * - **Plugin**
     - Additional platforms can be added via the gateway plugin interface. See
       ``docs/gateway-plugins.md`` in the repository.

Configuring a Channel
---------------------

Open the **Gateway** panel from the sidebar (the plug icon).

1. Click **Add Channel**.
2. Fill in the channel configuration form:

   - **Platform** — select Discord, WeChat, or a plugin provider.
   - **Channel name** — a friendly label for this connection (e.g., ``lab-discord-bot``).
   - **Credentials / Token** — paste the bot token (Discord) or leave blank to trigger
     QR login (WeChat).
   - **Target agent or team** — select which agent or team handles messages from this
     channel. If a team is selected, the team's routing logic determines which agent
     responds.
   - **Memory namespace** (optional) — by default, each platform user maps to their own
     Pantheon memory namespace. Override this to share memory across channels.

3. Click **Save**. The channel configuration is written to ``.pantheon/gateway.json``.

4. Click **Start** to activate the channel.

Starting & Stopping Channels
-----------------------------

Each configured channel has an on/off toggle in the Gateway panel.

- **Start** — the gateway process connects to the external platform and begins routing
  messages. A green indicator shows when the channel is connected.
- **Stop** — the gateway disconnects cleanly. In-flight messages complete before the
  connection closes.
- **Status** — the status indicator shows: Connecting / Connected / Disconnected /
  Error. Click the status badge to see a brief error message if the connection failed.

**Recent logs**

Click **Logs** on a channel row to tail the most recent gateway log entries for that
channel. Logs show incoming messages, routing decisions, response delivery, and any
errors.

WeChat QR Login
---------------

WeChat uses a QR-code session login rather than a static API token.

1. Add a WeChat channel and leave the token field blank.
2. Click **Start**. The Gateway panel displays a scannable QR code.
3. Open WeChat on your phone, go to **Discover → Scan**, and scan the code.
4. The gateway logs confirm the session is authenticated (``WeChat session active``).
5. The QR panel disappears and the channel shows **Connected**.

The session token is persisted to disk and refreshed automatically. Scanning is
required only once unless the session expires or is revoked on the WeChat side.

.. note::

   WeChat gateway uses personal account sessions, not the official WeChat Work or
   official account APIs. Session stability depends on WeChat's platform behavior.

Per-Channel Logs
----------------

Each channel maintains an in-memory rolling log of recent gateway events:

- Incoming message received (platform user ID, message preview, timestamp)
- Routing decision (target agent)
- Response delivered (character count, delivery status)
- Errors (API errors, timeouts, delivery failures)

Access logs from the **Logs** button in the channel row. Use **Download** to save the
full log to a file for debugging.

Scoping
-------

Each channel maps to a specific agent or team. Messages arriving on that channel are
processed exactly as if they arrived through the chat UI:

- They enter the same conversation memory as other chat sessions (scoped by platform
  user ID as the session namespace).
- The agent has access to all its configured toolsets.
- File attachments sent on Discord are downloaded and made available as context.
- Responses are streamed back to the platform as they are generated (Discord shows a
  typing indicator; WeChat delivers the complete response when generation finishes).

.. tip::

   To give a Discord bot access to files in your workspace, configure the target agent
   with the ``FileEditorToolSet``. Discord users can then ask the bot to read or create
   files and the agent will operate on the configured workspace.
