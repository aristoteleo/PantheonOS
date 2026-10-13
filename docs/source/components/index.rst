Core Components
===============

Deep dive into Pantheon's core building blocks.

Overview
--------

Pantheon is built on these core components:

.. code-block:: text

   ┌─────────────────────────────────────────┐
   │              Your Application            │
   └─────────────────┬───────────────────────┘
                     │
   ┌─────────────────▼───────────────────────┐
   │     Agent / Team (Orchestration)         │
   └─────────────────┬───────────────────────┘
                     │
   ┌────────┬────────┼────────┬──────────────┐
   │        │        │        │              │
   ▼        ▼        ▼        ▼              ▼
 Tools   Memory  Providers  Models       Config
   │        │        │        │              │
   └────────┴────────┴────────┴──────────────┘

Component Summary
-----------------

**Agents**
   The fundamental unit of intelligence. An agent combines a model with instructions and tools.

**Teams**
   Multiple agents working together. ``PantheonTeam`` supports team mode (agents delegate to each other) and agent mode (one agent starts sub-agents).

**Toolsets**
   Collections of tools that give agents capabilities like file access, code execution, and web search.

**Memory**
   Conversation history and context management. Supports persistence and compression.

**Providers**
   Abstractions for external tool sources (MCP servers, remote endpoints).

Relationships
-------------

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.team import Team
   from pantheon.toolsets import FileManagerToolSet
   from pantheon.memory import Memory
   from pantheon.providers import MCPProvider

   # Create an agent
   agent = Agent(
       name="assistant",
       instructions="You are a helpful assistant.",
       memory=Memory()
   )
   # Add toolsets at runtime
   await agent.toolset(FileManagerToolSet("files"))

   # A team coordinates multiple agents
   team = Team(
       name="dev_team",
       agents=[agent1, agent2, agent3]
   )

   # Providers connect to external tools via MCP
   agent_with_mcp = Agent(
       name="github_agent",
       instructions="You help with GitHub operations."
   )
   await agent_with_mcp.mcp(
       name="github",
       provider=MCPProvider("npx -y @anthropic/mcp-server-github")
   )

When to Use What
----------------

**Single Agent**
   Simple tasks, single domain expertise, straightforward workflows.

**Team**
   Complex tasks, multiple expertise areas, tasks requiring review/iteration.

**Custom Toolsets**
   When built-in tools don't cover your needs.

**MCP Providers**
   Integrating with external services, using community tools.

Related Sections
----------------

- :doc:`/api/agent` - Agent API reference
- :doc:`/team/index` - Team patterns and orchestration
- :doc:`/toolsets/index` - Tool system details
- :doc:`/api/memory` - Memory and context management
