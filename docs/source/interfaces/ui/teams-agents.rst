Teams & Agents
==============

The UI exposes full team configuration — you can choose templates, switch the active agent
mid-conversation, and manage agent models interactively without editing files or restarting
the backend.

Overview
--------

A **team** is a named collection of agents with defined roles, toolsets, and coordination
logic. The UI makes it easy to:

- Switch between team templates with a dropdown.
- See which agents are active and what each one can do.
- Change the active agent without ending the conversation.
- Override the model for a specific agent on the fly.
- Create and edit team templates directly in the browser.

Selecting a Team Template
--------------------------

The **Team** dropdown in the top navigation bar shows the currently active template. Click
it to open the template browser.

**Built-in templates**

Pantheon ships with a set of built-in templates covering common workflows:

- ``default`` — single general-purpose agent
- ``data_research_team`` — analyst + coder + critic
- ``single_cell`` — scRNA-seq analysis pipeline
- ``paper_writing`` — writer + editor + literature reviewer
- ``structural_biology`` — structure prediction + analysis
- ``coding_assistant`` — multi-agent code review and generation

**Custom templates**

Custom templates stored in ``.pantheon/teams/`` appear automatically in the dropdown.
Template files are Markdown documents with a structured YAML front matter block. See
:doc:`/configuration/templates/teams` for the format.

.. code-block:: text

   .pantheon/
   └── teams/
       ├── my_custom_team.md
       └── omics_pipeline.md

Switching the Active Agent
---------------------------

In a multi-agent conversation, only one agent is **active** at a time — subsequent messages
are routed to the active agent unless the team's coordination logic overrides this.

**Click an agent name**

Click any agent's colored name label above its response in the chat to switch the active
agent to that one. A brief confirmation indicator appears in the input bar.

**Agent switcher panel**

Open the **Agents** panel from the sidebar to see all agents in the team. Click an agent's
row to make it active.

**Slash command**

In the chat input, type ``/agent <name>`` or ``/agent <number>`` (using the agent's
position in the list) to switch programmatically.

.. note::

   Switching the active agent mid-conversation does not clear the conversation history.
   The new agent receives the same context window as the previous one, so it can reference
   earlier turns.

Per-Agent Model Override
-------------------------

You can assign a different model to a specific agent without changing the team template
file. This is useful for testing a higher-capability model on a critical agent while
keeping cheaper models for routine tasks.

**From the Agents panel**

Open the **Agents** panel, click the model tag next to an agent's name, and select a
new model from the browser. The override is applied immediately for the next response.

**Persistence**

Per-agent model overrides are saved to ``.pantheon/settings.json`` under
``agent_model_overrides`` so they persist across restarts:

.. code-block:: json

   {
     "agent_model_overrides": {
       "Analyst": "openai/gpt-4o",
       "Coder": "anthropic/claude-opus-4-5"
     }
   }

To clear an override and return to the template default, click **Reset to default** in the
model selector for that agent.

Creating & Editing Templates
-----------------------------

The inline template editor lets you create or modify team templates without leaving the
browser.

**Open the editor**

Click **Edit Template** in the Team dropdown, or click **New Template** to start from
scratch. The editor opens as a split panel alongside the chat.

**Template format**

Templates use Markdown with YAML front matter. The editor provides syntax highlighting and
a live validation panel that shows errors as you type.

**Save and reload**

Click **Save** to write the template to ``.pantheon/teams/<name>.md``. The team is
reloaded immediately — the current conversation is preserved but the agent configuration
is updated.

**Validation**

The editor validates the template on save and reports:

- Unknown fields in the YAML front matter
- Agent names that conflict with built-in reserved names
- Missing required fields (``agents``, ``name``)

Listing Agents
--------------

Open the **Agents** panel from the sidebar to see all agents in the current team:

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Column
     - Example
     - Description
   * - **Name**
     - ``Analyst``
     - The agent's display name as defined in the template
   * - **Role**
     - ``Primary``
     - Role in the team (Primary, Subagent, Critic, etc.)
   * - **Model**
     - ``claude-opus-4-5``
     - Currently active model (template default or override)
   * - **Toolsets**
     - ``Shell, Python, RAG``
     - Toolsets available to this agent
   * - **Status**
     - ``Idle``
     - Current agent state (Idle / Generating / Waiting)

Click any row to switch the active agent or expand the row to see the agent's full system
prompt.
