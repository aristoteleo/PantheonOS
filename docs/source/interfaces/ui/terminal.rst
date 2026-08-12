Integrated Terminal (PTY)
=========================

The web and desktop app includes a full integrated terminal. The terminal is backed by a
real PTY process on the server and streams output live to your browser. You get the same
experience as a local terminal, with no SSH required.

Overview
--------

The integrated terminal is a genuine interactive shell — not a command runner that
collects output and displays it after the fact. Input and output stream bidirectionally
in real time. This makes it suitable for interactive programs (``vim``, ``python``,
``htop``, ``ipython``) as well as long-running processes where you need to watch output
as it arrives.

The terminal runs on the server-side process, so it has full access to the server's
filesystem and environment. On remote deployments, this means you get a shell on the
remote machine without setting up SSH.

Opening a Terminal
------------------

Click the **Terminal** icon in the sidebar (the ``>_`` icon). A new PTY session starts
in the agent's current workspace directory. The terminal panel opens at the bottom of the
UI (or in a split panel, depending on your layout setting).

You can resize the terminal panel by dragging its top edge, or detach it into a floating
window with the **Pop out** button.

Terminal Sessions
-----------------

**Multiple sessions**

Click the **+** button in the terminal tab bar to open additional terminal sessions.
Each session is an independent PTY process. Switch between sessions by clicking their
tabs. Sessions are labeled ``Terminal 1``, ``Terminal 2``, etc., and can be renamed by
double-clicking the tab.

**Persistence across reconnects**

PTY sessions survive browser reconnects. When you reload the page or reattach to the
backend after a network interruption, the terminal replays the scrollback buffer (up to
the configured buffer size, default 10,000 lines) so you can see what happened while you
were disconnected. The process on the server continues running during disconnects.

**Idle session cleanup**

Sessions that have been idle (no input or output) for longer than the configured idle
timeout (default: 4 hours) are automatically cleaned up to free resources. The session
tab greys out with an **Ended** badge if the session has been cleaned up. Click **Reopen**
to start a new session in the same working directory.

Shell Selection
---------------

The terminal starts the default login shell of the server OS user (determined by
``/etc/passwd`` or the ``SHELL`` environment variable). To specify a different shell, set
the default in **Settings → Terminal**:

.. code-block:: json

   {
     "terminal": {
       "default_shell": "/bin/zsh"
     }
   }

When opening a terminal programmatically (e.g., from an agent or via the PTY API), the
shell can be overridden per session.

Resize Handling
---------------

The terminal component sends resize events to the PTY process whenever the panel changes
dimensions. ``SIGWINCH`` is propagated to the running shell and any child processes. This
means ``vim``, ``less``, ``htop``, and other terminal-aware programs reflow correctly
when you resize the panel.

The column and row counts are shown in the bottom-right corner of the terminal panel and
update in real time.

Terminal vs Shell Toolset
--------------------------

The integrated terminal and the ``ShellToolSet`` serve different purposes:

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Aspect
     - Integrated Terminal
     - ShellToolSet
   * - **Interaction model**
     - Interactive, persistent PTY session
     - Discrete commands with captured output
   * - **Persistence**
     - Session persists; shell state carries over between commands
     - Each tool call spawns a subprocess; state is not retained between calls
   * - **Use case**
     - Interactive programs, long-running processes, manual work
     - Agent-controlled command execution within workflows
   * - **Who drives it**
     - Human user
     - Agent (LLM)
   * - **Output handling**
     - Streamed to the UI panel live
     - Returned as text in the tool result for the agent to read

For agent workflows, always use the ``ShellToolSet`` — it gives the agent clean,
capturable output. Use the terminal for interactive manual tasks, environment debugging,
or situations where you need a persistent shell state.

.. tip::

   You can use the terminal and the agent side by side. For example: run a long build in
   the terminal and ask the agent to monitor a log file or help you interpret error output
   that you paste into the chat.
