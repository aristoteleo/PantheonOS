MoA Team
========

Mixture of Agents (MoA) Team implements an ensemble approach where multiple proposer agents work on the same problem, and their outputs are synthesized by an aggregator agent.

Overview
--------

MoA Teams operate in two phases:

1. **Proposal Phase**: Multiple proposer agents process the input (optionally in parallel)
2. **Aggregation Phase**: An aggregator agent synthesizes all proposals into a final response

This approach provides:

- Diverse perspectives on problems
- More robust and comprehensive solutions
- Multi-layer refinement through repeated aggregation

Reference papers:

- `MoA: Mixture-of-Agents <https://arxiv.org/abs/2406.04692>`_
- `Self-MoA <https://arxiv.org/abs/2502.00674>`_

Basic Usage
-----------

Creating a MoA Team
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import MoATeam
   from pantheon import Agent

   # Create proposer agents with diverse perspectives
   technical_expert = Agent(
       name="technical_expert",
       instructions="Provide technical analysis focusing on implementation details.",
       model="gpt-4o-mini"
   )

   business_expert = Agent(
       name="business_expert",
       instructions="Analyze from a business perspective, considering ROI and strategy.",
       model="gpt-4o-mini"
   )

   user_expert = Agent(
       name="user_expert",
       instructions="Focus on user experience and usability aspects.",
       model="gpt-4o-mini"
   )

   # Create aggregator
   aggregator = Agent(
       name="aggregator",
       instructions="Synthesize expert opinions into a comprehensive response.",
       model="gpt-4o"
   )

   # Create MoA team
   team = MoATeam(
       proposers=[technical_expert, business_expert, user_expert],
       aggregator=aggregator
   )

   # Run the team
   result = await team.run("Should we migrate our system to microservices?")
   print(result.content)

Constructor Parameters
----------------------

.. list-table::
   :header-rows: 1
   :widths: 20 25 55

   * - Parameter
     - Type
     - Description
   * - ``proposers``
     - list[Agent]
     - List of agents that generate proposals. Each processes the input independently.
   * - ``aggregator``
     - Agent
     - Agent that synthesizes all proposals into the final response.
   * - ``layers``
     - int
     - Number of MoA layers (repeated aggregation rounds). Default: 1.
   * - ``parallel``
     - bool
     - If True, run proposers concurrently. If False, run sequentially. Default: True.

How It Works
------------

Single Layer (Default)
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   User Message
        |
        v
   +----+----+----+
   |    |    |    |  (parallel or sequential)
   v    v    v    v
   [P1] [P2] [P3] [P4]  <- Proposers
   |    |    |    |
   +----+----+----+
        |
        v
   [Aggregator] <- Synthesizes all proposals
        |
        v
   Final Response

Multi-Layer (layers > 1)
~~~~~~~~~~~~~~~~~~~~~~~~

With ``layers=2``:

.. code-block:: text

   User Message
        |
        v
   [Proposers] -> Layer 1 responses
        |
        v
   [Proposers] -> Layer 2 (refine based on Layer 1)
        |
        v
   [Aggregator] -> Final synthesis
        |
        v
   Final Response

Aggregation Template
--------------------

MoATeam uses a built-in template to format proposer responses for aggregation:

.. code-block:: text

   Below are responses from different AI models to the same query.
   Please carefully analyze these responses and generate a final answer that is:
   - Most accurate and comprehensive
   - Best aligned with the user's instructions
   - Free from errors or inconsistencies

   ### Query:
   {user_query}

   ### Responses:
   {responses}

   ### Final Answer:

Run Method
----------

.. code-block:: python

   result = await team.run(
       msg="Your query",
       proposer_kwargs={},    # kwargs passed to all proposers
       **aggregator_kwargs    # kwargs passed to aggregator
   )

**Parameters:**

- ``msg``: The input message (string or message list)
- ``proposer_kwargs``: Dict of kwargs passed to each proposer's ``run()``
- ``**aggregator_kwargs``: Additional kwargs passed to the aggregator's ``run()``

**Example:**

.. code-block:: python

   result = await team.run(
       "Analyze the market trends",
       proposer_kwargs={"max_iterations": 3},
       stream=True  # Only applied to aggregator
   )

Parallel vs Sequential
----------------------

Parallel Execution (Default)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   team = MoATeam(
       proposers=[expert1, expert2, expert3],
       aggregator=synthesizer,
       parallel=True  # Default
   )

   # All proposers run concurrently
   result = await team.run("Query")

Sequential Execution
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   team = MoATeam(
       proposers=[expert1, expert2, expert3],
       aggregator=synthesizer,
       parallel=False
   )

   # Proposers run one after another
   result = await team.run("Query")

Multi-Layer MoA
---------------

Use multiple layers for iterative refinement:

.. code-block:: python

   team = MoATeam(
       proposers=[expert1, expert2, expert3],
       aggregator=synthesizer,
       layers=2  # Two rounds of proposal refinement
   )

   result = await team.run("Complex problem to solve")

With ``layers=2``:

1. First layer: All proposers process original query
2. Second layer: All proposers process aggregated result from layer 1
3. Final aggregation: Aggregator synthesizes layer 2 responses

Examples
--------

Decision Making Team
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import MoATeam
   from pantheon import Agent

   # Different analytical perspectives
   risk_analyst = Agent(
       name="risk_analyst",
       instructions="Analyze risks and potential negative outcomes.",
       model="gpt-4o"
   )

   opportunity_analyst = Agent(
       name="opportunity_analyst",
       instructions="Identify opportunities and benefits.",
       model="gpt-4o"
   )

   feasibility_analyst = Agent(
       name="feasibility_analyst",
       instructions="Assess technical and financial feasibility.",
       model="gpt-4o"
   )

   # Decision synthesizer
   decision_maker = Agent(
       name="decision_maker",
       instructions="""Synthesize all analyses into a recommendation:
       1. Summary of each perspective
       2. Key consensus points
       3. Major disagreements
       4. Final recommendation""",
       model="gpt-4o"
   )

   decision_team = MoATeam(
       proposers=[risk_analyst, opportunity_analyst, feasibility_analyst],
       aggregator=decision_maker
   )

   result = await decision_team.run("Should we expand to the European market?")

Self-MoA (Same Model, Different Prompts)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use the same model with different instructions:

.. code-block:: python

   # Same model, different perspectives
   analyst1 = Agent(
       name="conservative_analyst",
       instructions="Take a conservative, risk-averse approach.",
       model="gpt-4o"
   )

   analyst2 = Agent(
       name="optimistic_analyst",
       instructions="Focus on growth opportunities and potential.",
       model="gpt-4o"
   )

   analyst3 = Agent(
       name="balanced_analyst",
       instructions="Provide a balanced, objective analysis.",
       model="gpt-4o"
   )

   aggregator = Agent(
       name="synthesizer",
       instructions="Combine perspectives into a balanced conclusion.",
       model="gpt-4o"
   )

   self_moa = MoATeam(
       proposers=[analyst1, analyst2, analyst3],
       aggregator=aggregator
   )

Research Synthesis Team
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets import WebToolSet

   literature_reviewer = Agent(
       name="literature_reviewer",
       instructions="Review academic sources and cite findings.",
       tools=[WebToolSet()]
   )

   data_analyst = Agent(
       name="data_analyst",
       instructions="Focus on quantitative data and statistics."
   )

   methodology_expert = Agent(
       name="methodology_expert",
       instructions="Evaluate research methods and validity."
   )

   synthesizer = Agent(
       name="synthesizer",
       instructions="""Create a comprehensive research summary:
       - Integrate findings from all analyses
       - Identify patterns and contradictions
       - Provide evidence-based conclusions"""
   )

   research_team = MoATeam(
       proposers=[literature_reviewer, data_analyst, methodology_expert],
       aggregator=synthesizer,
       layers=2  # Refine twice for better synthesis
   )

Best Practices
--------------

1. **Diverse Proposers**: Use agents with different perspectives and approaches
2. **Clear Aggregation**: Design aggregator instructions for effective synthesis
3. **Appropriate Layers**: Use 1 layer for simple tasks, 2+ for complex problems
4. **Model Selection**: Consider using a stronger model for the aggregator
5. **Parallel Execution**: Enable for better performance (default)
6. **Team Size**: 3-5 proposers typically work well
