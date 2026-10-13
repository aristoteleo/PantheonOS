Team
====

This section covers multi-agent collaboration in Pantheon. Teams enable multiple agents to work together effectively on complex tasks.

Overview
--------

``PantheonTeam`` is the team structure Pantheon provides. It runs a conversation's agents in one of two modes:

- **Team mode** (default): Every agent can discover the others with ``list_agents()`` and delegate to them with ``call_agent()``
- **Agent mode**: Only the first agent converses; the other agents are sub-agent profiles it starts with ``call_agent()``

For other collaboration patterns, extend the base ``Team`` class (see `Creating Custom Teams`_).

Team Modes
----------

Team Mode
~~~~~~~~~

The default mode. The first agent receives the user's message and can dynamically discover and delegate to specialist agents.

Best for:

- Most multi-agent workflows
- Dynamic task routing
- Hierarchical organizations
- Complex workflows requiring specialized expertise

.. code-block:: python

   from pantheon.team import PantheonTeam
   from pantheon.agent import Agent

   researcher = Agent(name="researcher", instructions="...")
   analyst = Agent(name="analyst", instructions="...")
   writer = Agent(name="writer", instructions="...")

   team = PantheonTeam([researcher, analyst, writer])
   await team.chat()

Each agent automatically gets:

- ``call_agent(agent_name, instruction)``: Delegate to a specific agent
- ``list_agents()``: Discover available agents and their capabilities

Agent Mode
~~~~~~~~~~

Only the first agent converses with the user. The remaining agents are sub-agent profiles: the first agent starts them with ``call_agent()`` on self-contained tasks, each in its own fresh context, and only the sub-agent's final answer comes back. Independent sub-agent calls in one turn run in parallel.

Best for:

- A single main agent that offloads broad searches or separate deliverables
- Keeping the main conversation's context small

.. code-block:: python

   from pantheon.team import PantheonTeam

   leader = Agent(name="leader", instructions="...")
   researcher = Agent(name="researcher", description="Searches the web", instructions="...")

   team = PantheonTeam([leader, researcher], mode="agent")
   await team.chat()

In agent mode only the first agent receives ``call_agent()``; its description lists the sub-agent profiles. ``list_agents()`` is not added.

Quick Example
-------------

Here's a complete example using PantheonTeam:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from pantheon.team import PantheonTeam

   async def main():
       # Create specialized agents
       researcher = Agent(
           name="researcher",
           instructions="Research topics thoroughly using web search."
       )

       writer = Agent(
           name="writer",
           instructions="Write clear, engaging content based on research."
       )

       editor = Agent(
           name="editor",
           instructions="Polish and improve the text for clarity."
       )

       # Create team
       content_team = PantheonTeam([researcher, writer, editor])

       # Interactive chat
       await content_team.chat()

       # Or run a single task
       result = await content_team.run("Write an article about AI safety")
       print(result.content)

   asyncio.run(main())

Team Features
-------------

PantheonTeam supports:

- **Message passing**: Automatic context sharing between agents
- **Memory sharing**: Optional shared memory across team members
- **Error handling**: Graceful handling of agent failures
- **Streaming**: Real-time response streaming
- **REPL integration**: Use with ``team.chat()`` for interactive sessions
- **Cost tracking**: Aggregate token and cost statistics across agents

Team Configuration
------------------

Using Templates
~~~~~~~~~~~~~~~

Define teams in ``.pantheon/teams/`` using frontmatter format:

.. code-block:: markdown

   ---
   name: content_team
   agents:
     - name: researcher
       instructions: Research topics thoroughly.
       model: gpt-4o-mini
     - name: writer
       instructions: Write engaging content.
       model: gpt-4o-mini
   ---

   # Content Team

   This team creates high-quality content.

Load the team:

.. code-block:: python

   from pantheon.factory import load_team

   team = load_team("content_team")
   await team.chat()

Best Practices
--------------

1. **Clear Roles**: Define distinct responsibilities for each agent
2. **Appropriate Size**: Keep teams between 2-5 agents for efficiency
3. **Use PantheonTeam**: Start with PantheonTeam unless you need a custom collaboration pattern
4. **Error Recovery**: Implement fallback strategies for critical workflows
5. **Testing**: Test team dynamics with various scenarios
6. **Monitoring**: Track performance and optimize agent interactions

Creating Custom Teams
---------------------

Need a collaboration pattern not covered by PantheonTeam? You can create custom teams by extending the base ``Team`` class. See :doc:`custom_team` for a complete guide with examples.

.. code-block:: python

   from pantheon.team.base import Team
   from pantheon.agent import Agent, AgentInput

   class MyCustomTeam(Team):
       def __init__(self, agents: list[Agent], my_param: str = "default"):
           super().__init__(agents)
           self.my_param = my_param

       async def run(self, msg: AgentInput, **kwargs):
           # Implement your collaboration logic
           for name, agent in self.agents.items():
               result = await agent.run(msg, **kwargs)
           return result

.. toctree::
   :hidden:
   :maxdepth: 1

   pantheon_team
   custom_team
