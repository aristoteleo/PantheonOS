5-Minute Tutorial
=================

This tutorial gets you from zero to a working agent in 5 minutes.

.. contents:: On this page
   :local:
   :depth: 2

Prerequisites
-------------

1. Python 3.10 or higher
2. An OpenAI API key (or another LLM provider)

Step 1: Install (1 minute)
--------------------------

.. code-block:: bash

   # Using uv (recommended)
   uv pip install pantheon-agents

   # Or using pip
   pip install pantheon-agents

Step 2: Set API Key (30 seconds)
--------------------------------

.. code-block:: bash

   export OPENAI_API_KEY="your-api-key-here"

Step 3: Start Chatting (30 seconds)
-----------------------------------

**Option A: REPL (Recommended)**

.. code-block:: bash

   pantheon cli

You'll see a prompt. Type your message and press Enter:

.. code-block:: text

   > Hello! What can you do?

**Option B: Python Script**

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent

   async def main():
       agent = Agent(
           name="assistant",
           instructions="You are a helpful assistant."
       )
       await agent.chat()

   asyncio.run(main())

Step 4: Add Tools (2 minutes)
-----------------------------

Make your agent more powerful by adding toolsets:

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets import FileManagerToolSet, ShellToolSet

   agent = Agent(
       name="developer",
       instructions="You are a developer assistant. Help with coding tasks."
   )

   # Add toolsets at runtime
   await agent.toolset(FileManagerToolSet("files"))  # Read/write/edit files
   await agent.toolset(ShellToolSet("shell"))        # Run shell commands

Now your agent can:

- Read and write files
- Search code with grep/glob
- Run shell commands

Step 5: Create a Team (1 minute)
--------------------------------

Multiple agents working together:

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.team import PantheonTeam

   researcher = Agent(
       name="researcher",
       instructions="You research topics thoroughly."
   )

   writer = Agent(
       name="writer",
       instructions="You write clear, engaging content."
   )

   team = PantheonTeam([researcher, writer])
   await team.chat()

What's Next?
------------

You've learned the basics! Here's where to go next:

.. raw:: html

   <div class="sd-container-fluid sd-sphinx-override sd-mb-4">
   <div class="sd-row sd-row-cols-1 sd-row-cols-md-2 sd-g-3">

   <div class="sd-col sd-d-flex-column">
   <div class="sd-card sd-sphinx-override sd-w-100 sd-shadow-sm">
   <a href="../interfaces/index.html" class="sd-stretched-link"></a>
   <div class="sd-card-body" style="text-align: center;">
   <svg class="card-icon-sm icon-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M10.5 1.5H8.25A2.25 2.25 0 0 0 6 3.75v16.5a2.25 2.25 0 0 0 2.25 2.25h7.5A2.25 2.25 0 0 0 18 20.25V3.75a2.25 2.25 0 0 0-2.25-2.25H13.5m-3 0V3h3V1.5m-3 0h3m-3 18.75h3" /></svg>
   <p><strong>Choose Your Interface</strong></p>
   <p>Deep dive into REPL, Web UI, or Python API</p>
   </div>
   </div>
   </div>

   <div class="sd-col sd-d-flex-column">
   <div class="sd-card sd-sphinx-override sd-w-100 sd-shadow-sm">
   <a href="../configuration/index.html" class="sd-stretched-link"></a>
   <div class="sd-card-body" style="text-align: center;">
   <svg class="card-icon-sm icon-cyan" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.212-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" /><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" /></svg>
   <p><strong>Configuration</strong></p>
   <p>Customize settings, templates, and models</p>
   </div>
   </div>
   </div>

   <div class="sd-col sd-d-flex-column">
   <div class="sd-card sd-sphinx-override sd-w-100 sd-shadow-sm">
   <a href="../toolsets/index.html" class="sd-stretched-link"></a>
   <div class="sd-card-body" style="text-align: center;">
   <svg class="card-icon-sm icon-orange" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M11.42 15.17 17.25 21A2.652 2.652 0 0 0 21 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 1 1-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 0 0 4.486-6.336l-3.276 3.277a3.004 3.004 0 0 1-2.25-2.25l3.276-3.276a4.5 4.5 0 0 0-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437 1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008Z" /></svg>
   <p><strong>Toolsets</strong></p>
   <p>Add powerful capabilities to your agents</p>
   </div>
   </div>
   </div>

   <div class="sd-col sd-d-flex-column">
   <div class="sd-card sd-sphinx-override sd-w-100 sd-shadow-sm">
   <a href="../team/index.html" class="sd-stretched-link"></a>
   <div class="sd-card-body" style="text-align: center;">
   <svg class="card-icon-sm icon-purple" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M18 18.72a9.094 9.094 0 0 0 3.741-.479 3 3 0 0 0-4.682-2.72m.94 3.198.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0 1 12 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 0 1 6 18.719m12 0a5.971 5.971 0 0 0-.941-3.197m0 0A5.995 5.995 0 0 0 12 12.75a5.995 5.995 0 0 0-5.058 2.772m0 0a3 3 0 0 0-4.681 2.72 8.986 8.986 0 0 0 3.74.477m.94-3.197a5.971 5.971 0 0 0-.94 3.197M15 6.75a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm6 3a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Zm-13.5 0a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Z" /></svg>
   <p><strong>Teams</strong></p>
   <p>Build multi-agent workflows</p>
   </div>
   </div>
   </div>

   </div>
   </div>

Quick Reference
---------------

**REPL Commands:**

- ``/help`` - Show commands
- ``/view <file>`` - View file
- ``/clear`` - Clear context
- ``/exit`` - Exit

**Common Toolsets:**

- ``FileManagerToolSet`` - File operations
- ``ShellToolSet`` - Shell commands
- ``PythonInterpreterToolSet`` - Python execution
- ``IntegratedNotebookToolSet`` - Jupyter notebooks
- ``WebToolSet`` - Web search

**Team Modes:**

- ``PantheonTeam`` - Smart delegation with ``call_agent()`` / ``list_agents()`` (team mode)
- ``PantheonTeam(..., mode="agent")`` - One agent that starts sub-agents with ``call_agent()`` (agent mode)
