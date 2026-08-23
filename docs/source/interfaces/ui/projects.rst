Projects & Workspaces
=====================

Projects let you organize separate workspaces — each project has its own file workspace,
memory store, and endpoint subprocess. Switching projects is like switching between completely
independent Pantheon environments without restarting the application.

Overview
--------

A project in Pantheon is a named container that ties together:

- A **workspace directory** — the root path on disk that agents read and write files under.
- An **isolated memory store** — conversation history and MEMORY.md files scoped to this
  project only.
- A **dedicated endpoint** — a separate backend subprocess (or embedded event loop) that
  serves the project's agents, so different projects can run different team configurations
  simultaneously.

Projects are especially useful when you work across multiple distinct codebases, datasets,
or research contexts and want clean separation between them.

Creating and Switching Projects
--------------------------------

**Register a new project**

Open **Settings → Projects** in the sidebar and click **Add Project**. Provide:

- **Name** — a short label shown in the project switcher (e.g., ``genomics-lab``).
- **Workspace path** — the absolute path to the directory this project works in
  (e.g., ``/home/user/projects/rna-seq-pipeline``).
- **Team template** (optional) — a default team to activate when this project is opened.

There is no ``pantheon project`` CLI command. Projects are registered and switched
from the web/desktop app only.

**Switch the active project**

Click the project name in the top-left project switcher dropdown. Pantheon stops the
current project's endpoint, saves its state, and starts the selected project's endpoint.
The conversation sidebar refreshes to show sessions for the selected project.

**Remove a project**

In **Settings → Projects**, click the trash icon next to a project. Removing a project
unregisters it from the UI — it does not delete the workspace directory or the memory files
on disk.

Per-Project Isolation
---------------------

Each project maintains complete isolation across three dimensions:

**Endpoint isolation**

Each project runs its own endpoint subprocess. The endpoint loads the team template and
toolsets configured for that project. Two projects can run different teams (e.g., one with
a single-agent coding assistant, another with a multi-agent bioinformatics pipeline)
without interfering.

**Memory isolation**

Conversation history, retrieved context, and MEMORY.md files are stored under the project's
memory subdirectory. Sessions from project A are never visible to project B, even if both
use the same model.

**File access isolation**

When workspace mode is enabled (see below), agents are restricted to the project's
workspace directory. An agent in project A cannot read or write files from project B's
workspace.

Workspace Mode
--------------

Workspace mode is a per-chat setting that restricts agent file access to the project's
workspace root. When enabled:

- All relative file paths the agent constructs resolve within the workspace root.
- Attempts to access paths outside the workspace root are blocked by the file toolset.
- The workspace root is shown in the chat header so you always know which directory the
  agent is operating in.

Toggle workspace mode from the **...** menu in the chat header or configure it as the
default in ``.pantheon/settings.json``:

.. code-block:: json

   {
     "workspace": {
       "restrict_to_workspace": true
     }
   }

.. note::

   Workspace mode is a soft guardrail — it governs tool-level file access, not OS-level
   permissions. Agents that construct and execute shell commands directly can still access
   files outside the workspace if the OS user has permission.

Memory Routing
--------------

Each project has its own conversation memory that persists independently from other projects.

**Per-project MEMORY.md**

Pantheon maintains a ``MEMORY.md`` file in each project's memory directory. This file
accumulates facts the agent has been asked to remember across sessions — for example,
preferred coding style, dataset locations, or recurring collaborator names. The agent
reads this file at the start of each session within the project.

**Memory directory layout**

.. code-block:: text

   .pantheon/memory/
   ├── genomics-lab/
   │   ├── MEMORY.md
   │   ├── session_abc123.jsonl
   │   └── session_def456.jsonl
   └── coding-assistant/
       ├── MEMORY.md
       └── session_xyz789.jsonl

**Clearing project memory**

In **Settings → Projects**, click **Clear Memory** next to a project to delete its session
files and reset MEMORY.md. This does not affect the workspace directory.

.. tip::

   If you want a project's agent to start each session with project-specific context
   (data paths, background papers, lab conventions), add that context to the project's
   MEMORY.md directly — it will be included in every new session automatically.
