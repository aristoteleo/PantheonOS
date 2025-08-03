Reasoning Module
================

.. module:: pantheon.reasoning

The reasoning module provides advanced reasoning capabilities for agents.

Overview
--------

The reasoning module enables agents to use advanced reasoning models like O1, Gemini Flash Thinking, and Deepseek-R1.

.. automodule:: pantheon.reasoning
   :members:
   :undoc-members:
   :show-inheritance:

Reasoning Models
----------------

Supported Models
~~~~~~~~~~~~~~~~

The module supports various reasoning-enhanced models:

- **OpenAI O1**: Advanced reasoning with chain-of-thought
- **Gemini Flash Thinking**: Fast reasoning capabilities
- **Deepseek-R1**: Specialized reasoning model

Usage Examples
--------------

Basic Reasoning Agent
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.reasoning import enable_reasoning

   # Create a reasoning-enabled agent
   reasoning_agent = Agent(
       name="reasoner",
       instructions="You are an expert problem solver. Think step by step.",
       model="o1-preview"  # or other reasoning models
   )

   # Complex reasoning task
   result = await reasoning_agent.run(
       "Solve this logic puzzle: ..."
   )

Think-Then-Act Pattern
~~~~~~~~~~~~~~~~~~~~~~

From the examples:

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.team import SequentialTeam

   # Thinking agent
   thinker = Agent(
       name="thinker",
       instructions="Think through problems step by step",
       model="o1-mini"  # Reasoning model
   )

   # Acting agent  
   actor = Agent(
       name="actor",
       instructions="Execute based on the thinking",
       model="gpt-4o-mini"
   )

   # Think-then-act team
   team = SequentialTeam([thinker, actor])

Advanced Reasoning
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Multi-step reasoning
   agent = Agent(
       name="multi_step_reasoner",
       instructions="""
       For complex problems:
       1. Break down the problem
       2. Analyze each component
       3. Synthesize a solution
       4. Verify the result
       """,
       model="o1-preview"
   )

   # Mathematical reasoning
   math_result = await agent.run(
       "Prove that the square root of 2 is irrational"
   )

   # Logical reasoning
   logic_result = await agent.run(
       "Given premises A→B, B→C, and A is true, what can we conclude?"
   )

Reasoning Configuration
-----------------------

Model-Specific Settings
~~~~~~~~~~~~~~~~~~~~~~~

Different reasoning models may have specific configurations:

.. code-block:: python

   # O1 model configuration
   o1_agent = Agent(
       name="o1_reasoner",
       instructions="Use careful reasoning",
       model="o1-preview",
       temperature=1,  # O1 works best at temperature=1
       max_tokens=4096  # Allow longer reasoning chains
   )

   # Deepseek configuration
   deepseek_agent = Agent(
       name="deepseek_reasoner",
       instructions="Apply deep reasoning",
       model="deepseek-r1",
       force_litellm=True  # May need litellm backend
   )

Best Practices
--------------

1. **Clear Instructions**: Provide explicit reasoning instructions
2. **Appropriate Tasks**: Use for complex problems requiring step-by-step thinking
3. **Token Limits**: Reasoning models may use more tokens
4. **Temperature Settings**: Some models work best at specific temperatures
5. **Response Time**: Reasoning models may take longer to respond

Integration with Teams
----------------------

Reasoning in Teams
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import MoATeam

   # Multiple reasoning agents
   reasoner1 = Agent(
       name="reasoner1",
       instructions="Approach from perspective A",
       model="o1-mini"
   )

   reasoner2 = Agent(
       name="reasoner2", 
       instructions="Approach from perspective B",
       model="gemini-flash-thinking"
   )

   synthesizer = Agent(
       name="synthesizer",
       instructions="Combine reasoning approaches",
       model="gpt-4"
   )

   # Reasoning ensemble
   reasoning_team = MoATeam([reasoner1, reasoner2, synthesizer])

Examples from Repository
------------------------

The repository includes reasoning examples:

- ``examples/chatbots/reasoning_bot.py``: Basic reasoning bot
- ``examples/team/think_then_act.py``: Think-then-act pattern

These demonstrate practical applications of reasoning capabilities.

Performance Considerations
--------------------------

- **Latency**: Reasoning models may have higher latency
- **Cost**: Advanced reasoning models may be more expensive
- **Context**: Reasoning benefits from clear problem statements
- **Verification**: Consider adding verification steps

Future Enhancements
-------------------

The reasoning module is actively developed with planned support for:

- Additional reasoning models
- Custom reasoning chains
- Reasoning metrics and evaluation
- Hybrid reasoning approaches