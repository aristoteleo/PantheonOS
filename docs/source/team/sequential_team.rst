Sequential Team
===============

SequentialTeam processes tasks through a series of agents in a predefined order, with each agent building upon the work of the previous one.

Overview
--------

In a Sequential Team:

- Agents execute in a fixed order
- Each agent receives the accumulated conversation history
- A connect prompt is injected between agents to guide transitions
- The final agent's output becomes the team's response

Basic Usage
-----------

Creating a Sequential Team
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SequentialTeam
   from pantheon import Agent

   # Create specialized agents
   researcher = Agent(
       name="researcher",
       instructions="Research the topic and gather relevant information.",
       model="gpt-4o-mini"
   )

   analyst = Agent(
       name="analyst",
       instructions="Analyze the research and identify key insights.",
       model="gpt-4o-mini"
   )

   writer = Agent(
       name="writer",
       instructions="Create a well-structured report based on the analysis.",
       model="gpt-4o-mini"
   )

   # Create sequential team
   team = SequentialTeam([researcher, analyst, writer])

   # Run the team
   result = await team.run("Analyze the impact of AI on healthcare")
   print(result.content)

Constructor Parameters
----------------------

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Parameter
     - Type
     - Description
   * - ``agents``
     - list[Agent]
     - List of agents to run in sequence. Order determines execution order.
   * - ``connect_prompt``
     - str | list[str]
     - Prompt injected between agents to guide transitions. Default: "Next:".

Connect Prompts
---------------

The ``connect_prompt`` is injected as a user message between each agent's response and the next agent's turn. This guides the handoff between agents.

Default Connect Prompt
~~~~~~~~~~~~~~~~~~~~~~

By default, "Next:" is injected between agents:

.. code-block:: python

   team = SequentialTeam([agent1, agent2, agent3])
   # Flow: agent1 -> "Next:" -> agent2 -> "Next:" -> agent3

Custom Connect Prompt
~~~~~~~~~~~~~~~~~~~~~

Use a single prompt for all transitions:

.. code-block:: python

   team = SequentialTeam(
       [researcher, analyst, writer],
       connect_prompt="Please continue with your specialized analysis:"
   )

Per-Transition Prompts
~~~~~~~~~~~~~~~~~~~~~~

Use a list to specify different prompts for each transition:

.. code-block:: python

   team = SequentialTeam(
       [researcher, analyst, writer],
       connect_prompt=[
           "Now analyze these research findings:",  # researcher -> analyst
           "Based on this analysis, write the final report:"  # analyst -> writer
       ]
   )

Run Method
----------

The ``run()`` method executes the sequential pipeline:

.. code-block:: python

   result = await team.run(
       msg="Your task message",
       connect_prompt=None,  # Override connect_prompt for this run
       agent_kwargs={},      # Per-agent kwargs: {"agent_name": {...}}
       **final_kwargs        # Additional kwargs passed to final agent only
   )

**Parameters:**

- ``msg``: The input message (string or message list)
- ``connect_prompt``: Override the team's default connect prompt
- ``agent_kwargs``: Dict mapping agent names to their run() kwargs
- ``**final_kwargs``: Additional kwargs passed only to the final agent

**Example with agent_kwargs:**

.. code-block:: python

   result = await team.run(
       "Research renewable energy trends",
       agent_kwargs={
           "researcher": {"max_iterations": 5},
           "analyst": {"temperature": 0.3}
       },
       stream=True  # Only applied to final agent (writer)
   )

How It Works
------------

.. code-block:: text

   User Message
        |
        v
   [Researcher] processes message
        |
        v
   Connect Prompt: "Next:"
        |
        v
   [Analyst] receives full history + connect prompt
        |
        v
   Connect Prompt: "Next:"
        |
        v
   [Writer] receives full history + connect prompts
        |
        v
   Final Response

The conversation history accumulates through the pipeline:

1. User message sent to first agent
2. First agent responds
3. Connect prompt added to history
4. Second agent sees: user message + first agent response + connect prompt
5. Process repeats until final agent

Examples
--------

Research Pipeline
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SequentialTeam
   from pantheon import Agent
   from pantheon.toolsets import WebToolSet, FileManagerToolSet

   researcher = Agent(
       name="researcher",
       instructions="Research the topic using web search.",
       tools=[WebToolSet()]
   )

   analyst = Agent(
       name="analyst",
       instructions="Analyze the research findings and extract insights."
   )

   writer = Agent(
       name="writer",
       instructions="Write a comprehensive report.",
       tools=[FileManagerToolSet()]
   )

   team = SequentialTeam(
       [researcher, analyst, writer],
       connect_prompt=[
           "Analyze these research findings:",
           "Write a report based on this analysis:"
       ]
   )

   result = await team.run("Research the current state of quantum computing")

Code Review Pipeline
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   architect = Agent(
       name="architect",
       instructions="Review the code architecture and design."
   )

   security_reviewer = Agent(
       name="security",
       instructions="Identify security vulnerabilities."
   )

   quality_reviewer = Agent(
       name="quality",
       instructions="Summarize all findings and recommendations."
   )

   review_team = SequentialTeam(
       [architect, security_reviewer, quality_reviewer],
       connect_prompt=[
           "Now check for security issues:",
           "Summarize all review findings:"
       ]
   )

Best Practices
--------------

1. **Clear Instructions**: Each agent should know its role in the pipeline
2. **Meaningful Connect Prompts**: Use prompts that guide the next agent's focus
3. **Logical Ordering**: Place agents in order of dependencies
4. **Appropriate Team Size**: Keep pipelines to 2-5 agents for efficiency
5. **Specialized Agents**: Each agent should have a distinct responsibility
