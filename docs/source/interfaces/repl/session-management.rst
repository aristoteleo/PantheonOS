Session Management
==================

Pantheon CLI automatically saves every conversation as a named session. Sessions persist
across restarts and can be resumed, exported, or recovered after a crash.

Overview
--------

Every time you start a new chat with ``pantheon cli`` (or type ``/new``), Pantheon creates
a session with:

- A unique **session ID** — a short alphanumeric identifier (e.g., ``8f3a2b1c``).
- An auto-generated **name** derived from the first few words of your first message
  (e.g., ``rna-seq-quality-control``).
- A **timestamp** and message count that update with each turn.

Sessions are written to the memory directory after every turn. If the process crashes or
you close the terminal mid-response, the session is recoverable from disk.

How Sessions Work
-----------------

Sessions are stored as JSONL files in the memory directory:

.. code-block:: text

   .pantheon/memory/
   ├── 8f3a2b1c_rna-seq-quality-control.jsonl
   ├── d2e4f6a8_protein-structure-prediction.jsonl
   └── MEMORY.md

Each line in the JSONL file is a JSON object representing one conversation turn (user
message, assistant message, or tool call/result). The file is append-only during normal
operation.

The memory directory defaults to ``.pantheon/memory/`` relative to the workspace. Change
it with ``--memory-dir``:

.. code-block:: bash

   pantheon cli --memory-dir ~/pantheon-sessions

Listing Sessions
----------------

Use ``/list`` or its alias ``/chats`` to see all saved sessions:

.. code-block:: text

   > /list

   ID        Name                          Date          Messages
   ────────  ────────────────────────────  ────────────  ────────
   8f3a2b1c  rna-seq-quality-control       2026-08-10    47
   d2e4f6a8  protein-structure-prediction  2026-08-09    23
   c1b9e7f2  variant-annotation-pipeline   2026-08-07    91

Sessions are listed in reverse chronological order (most recent first). The most recent
session is marked with an arrow indicator.

Resuming a Session
------------------

There are three ways to resume a session:

**Most recent session**

.. code-block:: bash

   # From the command line before starting
   pantheon cli --resume last

   # Or from inside the REPL
   /resume last

**By partial name**

.. code-block:: bash

   pantheon cli --resume rna-seq

   # Matches the first session whose name contains "rna-seq" (case-insensitive)

**By session ID**

.. code-block:: bash

   pantheon cli --resume 8f3a2b1c

   # Exact ID match
   /resume 8f3a2b1c

After resuming, the full conversation history is displayed and you can continue from
where you left off. The agent receives the complete prior context.

Renaming a Session
------------------

Session names are set automatically from the first message, and the CLI has no rename
command. A session's name is part of its file name on disk
(``<id>_<name>.jsonl``), so renaming means renaming that file while the session is not
open — do it at your own risk, and keep the ``<id>_`` prefix intact so ``/resume`` can
still find it.

.. warning::

   Do **not** try to rename a session by exporting it, deleting the original, and
   loading the export back. ``/load`` does not import anything (see
   `Exporting Sessions`_ below), so deleting the original would lose the conversation.

Forking a Session
-----------------

There is no built-in fork or branch command. What the CLI offers instead:

- ``/revert [index]`` rewinds the *current* session to an earlier user turn and lets you
  continue in a new direction from there. This rewrites the session in place rather than
  creating a second one, and it only affects conversation memory — files the agent
  already wrote, and any other external state, are not rolled back.
- ``/save <file>`` writes a portable copy of the conversation before you revert, so you
  keep a record of the path you abandoned.

Exporting Sessions
------------------

``/save`` writes the current conversation to a portable JSON file:

.. code-block:: text

   > /save
   ✅ Conversation saved to: 20260810_143201.json

   > /save /tmp/analysis-backup.json
   ✅ Conversation saved to: /tmp/analysis-backup.json

With no argument the file name is a timestamp (``YYYYmmdd_HHMMSS.json``) in the current
directory. A name you supply gets a ``.json`` suffix if it does not already have one.

The file is a single JSON object with the session ``id``, its ``name``, the ``messages``
list, and ``extra_data``. It is a snapshot for archiving, sharing, or feeding to a
downstream script.

.. important::

   Export is **one-way**. ``/load <file>`` is not implemented in the current
   ChatRoom-based CLI: it prints a notice recommending ``/resume`` and does not read the
   file or change the conversation. Treat ``/save`` output as an archive, not as
   something you can import back into a session.

Returning to a Saved Conversation
---------------------------------

To pick a conversation back up, resume the session itself rather than loading a file:

.. code-block:: text

   > /list                  # find the session
   > /resume 8f3a2b1c       # or /resume rna-seq, or /resume last

Sessions live in the memory directory and are resumable indefinitely, so there is no
need to export and re-import to continue earlier work.

Auto-Recovery
-------------

If Pantheon CLI exits unexpectedly mid-response (crash, OOM, SIGKILL), the partial
response may not be written cleanly to the session file. On the next startup, Pantheon
detects incomplete sessions and offers to recover:

.. code-block:: text

   ╭─ Recovery ─────────────────────────────────────────────╮
   │ Session "rna-seq-quality-control" has an incomplete     │
   │ last response. Recover and continue? [y/N]              │
   ╰────────────────────────────────────────────────────────╯

- **y** — the partial response is trimmed to the last complete sentence and the
  session is resumed.
- **N** — the session is loaded without the incomplete turn; you can re-send the
  last message.

Session Storage Format
----------------------

Each JSONL file stores turns sequentially. Each turn object contains:

.. code-block:: json

   {
     "role": "user",
     "content": "Explain the QC metrics in this plot",
     "timestamp": "2026-08-10T14:32:01Z",
     "attachments": [{"type": "file", "path": "figures/qc_violin.png"}]
   }

Tool calls and results are stored as additional turn objects with ``"role": "tool"``.
MEMORY.md is a plain Markdown file written and read by the agent to persist facts across
sessions.

.. tip::

   To inspect a session file directly, use any JSONL viewer or:

   .. code-block:: bash

      cat .pantheon/memory/8f3a2b1c_rna-seq-quality-control.jsonl | python -m json.tool
