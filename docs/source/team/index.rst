Team
====

This section covers the various team collaboration patterns available in Pantheon. Teams enable multiple agents to work together effectively on complex tasks.

Overview
--------

Pantheon provides several team structures to match different collaboration needs:

- **PantheonTeam** (Recommended): Intelligent delegation with ``call_agent()`` and ``list_agents()``
- **Sequential Team**: Agents work in a predefined order
- **MoA Team**: Multiple agents work independently, then synthesize
- **Swarm Team**: Dynamic agent selection based on needs
- **SwarmCenter Team**: Central coordinator manages worker agents
- **AgentAsToolTeam**: Leader treats sub-agents as tools

Choosing a Team Type
--------------------

PantheonTeam (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~

The default team type with intelligent agent delegation. A lead agent can dynamically discover and delegate to specialist agents.

Best for:

- Most multi-agent workflows
- Dynamic task routing
- Hierarchical organizations
- Complex workflows requiring specialized expertise

.. code-block:: python

   from pantheon.team import PantheonTeam
   from pantheon import Agent

   researcher = Agent(name="researcher", instructions="...")
   analyst = Agent(name="analyst", instructions="...")
   writer = Agent(name="writer", instructions="...")

   team = PantheonTeam([researcher, analyst, writer])
   await team.chat()

The lead agent automatically gets:

- ``call_agent(name, instruction)``: Delegate to a specific agent
- ``list_agents()``: Discover available agents and their capabilities

Sequential Team
~~~~~~~~~~~~~~~

Best for:

- Multi-step workflows with clear stages
- Pipeline processing
- Tasks with dependencies between stages

.. code-block:: python

   from pantheon.team import SequentialTeam

   team = SequentialTeam([researcher, analyst, writer])

MoA (Mixture of Agents) Team
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Best for:

- Getting diverse perspectives
- Robust decision making
- Complex problem solving

.. code-block:: python

   from pantheon.team import MoATeam

   team = MoATeam(
       proposers=[expert1, expert2, expert3],
       aggregator=synthesizer
   )

Swarm Team
~~~~~~~~~~

Best for:

- Dynamic workflows
- Flexible task routing
- Adaptive problem solving

.. code-block:: python

   from pantheon.team import SwarmTeam

   @agent1.tool
   def transfer_to_agent2():
       """Transfer to agent2 for specialized work."""
       return agent2

   team = SwarmTeam([agent1, agent2])

SwarmCenter Team
~~~~~~~~~~~~~~~~

Best for:

- Complex coordination
- Task distribution
- Centralized management

.. code-block:: python

   from pantheon.team import SwarmCenterTeam

   team = SwarmCenterTeam(
       triage=coordinator,
       agents=[worker1, worker2, worker3]
   )

AgentAsToolTeam
~~~~~~~~~~~~~~~

Best for:

- Hierarchical control
- Sub-agents as specialized tools
- Clear leader-worker patterns

.. code-block:: python

   from pantheon.team import AgentAsToolTeam

   team = AgentAsToolTeam(
       leader=main_agent,
       agents=[specialist1, specialist2]
   )

Quick Example
-------------

Here's a complete example using PantheonTeam:

.. code-block:: python

   import asyncio
   from pantheon import Agent
   from pantheon.team import PantheonTeam

   async def main():
       # Create specialized agents
       researcher = Agent(
           name="researcher",
           instructions="Research topics thoroughly using web search.",
           model="gpt-4o-mini"
       )

       writer = Agent(
           name="writer",
           instructions="Write clear, engaging content based on research.",
           model="gpt-4o-mini"
       )

       editor = Agent(
           name="editor",
           instructions="Polish and improve the text for clarity.",
           model="gpt-4o-mini"
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

All teams support:

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
3. **Use PantheonTeam**: Start with PantheonTeam unless you have specific needs
4. **Error Recovery**: Implement fallback strategies for critical workflows
5. **Testing**: Test team dynamics with various scenarios
6. **Monitoring**: Track performance and optimize agent interactions

.. toctree::
   :hidden:
   :maxdepth: 1

   pantheon_team
   sequential_team
   moa_team
   swarm_team
   swarm_center_team
