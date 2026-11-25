Core Chat & Session Features
============================

This page covers the primary chat and session capabilities of the Pantheon web and desktop app.

Chat Interface
--------------

The chat panel is the central surface for communicating with agents.

**Sending messages**

Type your message in the input box and press **Enter** to send. Responses stream token-by-token
as the agent generates them; a blinking cursor indicates active generation.

**Multi-agent attribution**

When a team has more than one agent, each agent's name is displayed above its response in a
distinct color. This makes it easy to follow which agent produced which output in long
multi-turn conversations.

**File and image attachments**

Click the attachment icon (or drag and drop) to attach files to a message. Any text-readable
file format is sent as context. Images are sent as vision input when the active model supports
it. Multiple files can be attached to a single message.

**Speech-to-text**

Click the microphone icon in the input bar to record a voice message. Pantheon transcribes the
audio using the configured speech-to-text model and inserts the transcript into the input box
for review before sending.

.. tip::

   Speech-to-text requires a speech model to be configured in ``.pantheon/settings.json``.
   See :doc:`/configuration/settings` for the ``speech_to_text_model`` option.

Sessions
--------

Each conversation is stored as a named session that persists across restarts.

**Create a new session**

Click **New Chat** in the sidebar or use the ``/new`` slash command in the input bar. A new
session starts immediately with a fresh conversation history.

**Rename a session**

Double-click the session name in the sidebar to rename it inline. Names are stored in the
session metadata and appear in resume prompts.

**Delete a session**

Right-click a session in the sidebar and select **Delete**. Deletion is permanent — the
conversation history is removed from the memory store.

**Fork a session**

Right-click a session and select **Fork**. A new session is created that starts from the
same conversation history up to the current point. Both sessions evolve independently from
that point forward.

**Session persistence**

Sessions are written to the memory directory (default: ``.pantheon/memory/``) after every
turn. If the app or server restarts, sessions are automatically reloaded and available in
the sidebar.

**Resume a session**

Click any session in the sidebar to open it and resume the conversation. The full history
is displayed and you can continue from where you left off.

Context Management
------------------

**Token usage display**

The token counter in the input bar shows the approximate number of tokens in the current
context window. The counter updates after each turn.

**Context compression**

When the conversation approaches the model's context limit, click **Compress** or type
``/compress`` to trigger summarization. The agent condenses the earlier conversation into a
compact summary, freeing context space while preserving key information.

.. note::

   Compression is destructive — the verbatim earlier turns are replaced by a summary.
   The raw session file is preserved on disk so you can reload it if needed.

**Revert to an earlier point**

Hover over any user message in the chat and click **Revert here**. The conversation is
truncated to that point and you can continue from a different direction. The reverted turns
are not deleted from the session file — they are marked inactive so you can revert the
revert if needed.

Keyboard Shortcuts
------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Key
     - Action
   * - **Enter**
     - Send the current message
   * - **Shift+Enter**
     - Insert a newline (multi-line input)
   * - **Esc**
     - Cancel the currently streaming generation
   * - **Ctrl+/** (or **Cmd+/**)
     - Focus the input box from anywhere in the UI
   * - **Up Arrow** (in empty input)
     - Load the previous message you sent (edit and resend)
   * - **Ctrl+K**
     - Open the command palette / quick-switch
   * - **Ctrl+Shift+N**
     - Open a new chat session
   * - **Ctrl+Shift+F**
     - Search across all sessions

Background Tasks
----------------

Some agent operations run asynchronously (file indexing, long-running analyses, batch jobs).
These appear as **background tasks** rather than blocking the chat.

**Task list panel**

Click the **Tasks** icon in the sidebar to open the task list. Each task shows its name,
the agent that started it, current status (queued / running / complete / error), and
elapsed time.

**Cancel a task**

Click the **Cancel** button next to a running task. The agent receives a cancellation
signal and stops at the next safe checkpoint.

**Monitoring**

Progress updates from background tasks appear as streaming status messages in the chat.
When a task completes, a notification badge appears on the Tasks icon and a brief
summary message is posted to the conversation.

.. tip::

   Use background tasks for operations that take more than a few seconds — for example,
   running a full analysis pipeline while continuing to chat with a different agent.

Theme & Display
---------------

Click the **Sun / Moon** icon in the top navigation bar to toggle between light and dark mode.
The preference is saved in local browser storage and persists across sessions.

The sidebar can be collapsed with the **←** button to give the chat panel more horizontal
space. On narrow viewports the sidebar auto-collapses.
