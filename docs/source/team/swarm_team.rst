Swarm Team
==========

SwarmTeam enables dynamic agent collaboration where agents can transfer control to each other using handoff patterns, similar to OpenAI's Swarm framework.

Overview
--------

Key features of Swarm Teams:

- **Dynamic Routing**: Agents decide when to transfer control
- **Flexible Workflows**: No predefined execution order
- **Context Preservation**: Memory tracks active agent across transfers
- **Transfer via Tools**: Agents transfer by returning another agent from a tool

Basic Usage
-----------

Creating a Swarm Team
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SwarmTeam
   from pantheon import Agent

   # Create specialized agents
   generalist = Agent(
       name="generalist",
       instructions="Handle general queries. Transfer specialized tasks to experts.",
       model="gpt-4o-mini"
   )

   tech_expert = Agent(
       name="tech_expert",
       instructions="Handle technical questions about programming.",
       model="gpt-4o"
   )

   creative_expert = Agent(
       name="creative_expert",
       instructions="Handle creative tasks like writing.",
       model="gpt-4o"
   )

   # Define transfer functions - return an Agent to transfer control
   @generalist.tool
   def transfer_to_tech_expert():
       """Transfer technical questions to the tech expert."""
       return tech_expert

   @generalist.tool
   def transfer_to_creative_expert():
       """Transfer creative tasks to the creative expert."""
       return creative_expert

   @tech_expert.tool
   def transfer_to_generalist():
       """Transfer back to generalist for non-technical matters."""
       return generalist

   @creative_expert.tool
   def transfer_to_generalist():
       """Transfer back to generalist for non-creative matters."""
       return generalist

   # Create swarm team
   team = SwarmTeam([generalist, tech_expert, creative_expert])

   # Run the team
   await team.chat()

Constructor Parameters
----------------------

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Parameter
     - Type
     - Description
   * - ``agents``
     - list[Agent | RemoteAgent]
     - List of agents in the swarm. First agent is the default entry point.

Transfer Mechanism
------------------

Agents transfer control by returning another Agent from a tool function:

.. code-block:: python

   @agent_a.tool
   def transfer_to_agent_b():
       """Transfer to agent B."""
       return agent_b  # Return the Agent object to transfer

When a tool returns an Agent, the SwarmTeam:

1. Stores the transfer in memory
2. Runs the target agent on the next iteration
3. Continues until an agent returns without transferring

How It Works
------------

.. code-block:: text

   User Message
        |
        v
   [Active Agent] (first agent by default)
        |
        +---> Processes message
        |
        +---> If tool returns Agent: transfer control
        |          |
        |          v
        |     [New Active Agent] becomes active
        |
        +---> If no transfer: return response

The active agent is tracked in the Memory's extra_data:

.. code-block:: python

   memory.extra_data["active_agent"]  # Current active agent name

Run Method
----------

.. code-block:: python

   result = await team.run(
       msg="Your message",
       memory=None,  # Optional Memory object for state persistence
       **kwargs      # Additional kwargs passed to agent.run()
   )

**Parameters:**

- ``msg``: The input message
- ``memory``: Optional Memory object to track active agent state
- ``**kwargs``: Additional kwargs passed to the active agent

Examples
--------

Customer Support Swarm
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SwarmTeam
   from pantheon import Agent

   # Create support agents
   greeter = Agent(
       name="greeter",
       instructions="Welcome customers and route to appropriate support."
   )

   technical_support = Agent(
       name="technical_support",
       instructions="Resolve technical issues with step-by-step solutions."
   )

   billing_support = Agent(
       name="billing_support",
       instructions="Handle billing inquiries and payment issues."
   )

   # Define routing
   @greeter.tool
   def route_to_tech():
       """Route to technical support."""
       return technical_support

   @greeter.tool
   def route_to_billing():
       """Route to billing support."""
       return billing_support

   @technical_support.tool
   def route_back_to_greeter():
       """Transfer back to greeter."""
       return greeter

   @billing_support.tool
   def route_back_to_greeter():
       """Transfer back to greeter."""
       return greeter

   # Create support swarm
   support_swarm = SwarmTeam([greeter, technical_support, billing_support])
   await support_swarm.chat()

Transfer with Context
~~~~~~~~~~~~~~~~~~~~~

Pass context information in transfer tool parameters:

.. code-block:: python

   @support_agent.tool
   def transfer_to_tech(issue_description: str):
       """Transfer technical issues with context.

       Args:
           issue_description: Description of the technical issue
       """
       return tech_support

   @support_agent.tool
   def transfer_to_billing(account_id: str, issue: str):
       """Transfer billing issues with account context.

       Args:
           account_id: Customer account ID
           issue: Description of billing issue
       """
       return billing_support

Persistent Memory
~~~~~~~~~~~~~~~~~

Use Memory to maintain state across interactions:

.. code-block:: python

   from pantheon.memory import Memory

   # Create persistent memory
   memory = Memory(name="support-session")

   # Run with memory - active agent persists
   result = await team.run("I need help", memory=memory)

   # Later in same session - active agent remembered
   result = await team.run("Follow up question", memory=memory)

Best Practices
--------------

1. **Clear Transfer Criteria**: Define when and why agents should transfer
2. **Avoid Infinite Loops**: Ensure agents can complete without endless transfers
3. **Descriptive Tool Docs**: Help agents understand when to use transfer tools
4. **Context Passing**: Use tool parameters to pass relevant context during transfers
5. **Specialized Agents**: Each agent should have clear expertise
