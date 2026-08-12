Session Management
==================

Pantheon CLI automatically saves every conversation as a named session. Sessions persist
across restarts and can be resumed, exported, branched, or recovered after a crash.

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

Session names are currently set automatically from the first message. To rename a
session, export it, delete the original, and reimport under a new name:

.. code-block:: text

   > /save my-rna-analysis.json
   > /new
   > /load my-rna-analysis.json

The loaded session inherits the filename as its display name. This workflow also serves
as a manual backup mechanism.

Forking a Session
-----------------

To branch the conversation from a specific point:

1. Export the conversation up to the desired point: ``/save branch-point.json``.
2. Start a new session: ``/new``.
3. Load the exported file: ``/load branch-point.json``.
4. Continue from the branch point in a new direction.

Both sessions (original and fork) are now independent and evolve separately.

Exporting Sessions
------------------

.. code-block:: text

   > /save
   Saved to: 8f3a2b1c_rna-seq-quality-control.json

   > /save /tmp/analysis-backup.json
   Saved to: /tmp/analysis-backup.json

The exported file is a JSON array of conversation turns. It can be shared with colleagues,
imported on another machine, or used as input to a downstream script.

Importing Sessions
------------------

.. code-block:: text

   > /load /tmp/analysis-backup.json

The conversation from the file is loaded into the current session. All prior turns
are available in the agent's context. You can continue the conversation or use
``/revert`` to go back to a specific earlier point.

.. note::

   Loading a file into a session that already has conversation history **appends** the
   loaded turns after the existing history. If you want a clean import, start a new
   session with ``/new`` first.

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
