Examples
========

Learn from practical examples demonstrating Pantheon's capabilities.

Available Examples
------------------

Chatbots
~~~~~~~~

Various specialized chatbot implementations:

- **search_bot.py**: Web search agent using DuckDuckGo
- **coderun_bot.py**: Python code execution agent  
- **reasoning_bot.py**: Advanced reasoning capabilities
- **r_bot.py**: R language execution
- **shell_bot.py**: Shell command execution
- **gemini.py**: Gemini model integration
- **deepseek.py**: Deepseek model integration

Teams
~~~~~

Different team collaboration patterns:

- **chat_with_seq_team.py**: Sequential team example
- **chat_with_swarm_team.py**: Swarm team with transfers
- **chat_with_swarm_center_team.py**: Center-coordinated team
- **chat_with_moa_team.py**: Mixture of Agents team
- **think_then_act.py**: Advanced reasoning pattern

Applications
~~~~~~~~~~~~

- **guess_number.py**: Interactive number guessing game
- **paper_reporter/**: Academic paper analysis system
- **paper_reporter_v2/**: Enhanced paper reporter

Running Examples
----------------

Basic Setup
~~~~~~~~~~~

1. Install Pantheon:

   .. code-block:: bash

      pip install pantheon-agents

2. Set API keys:

   .. code-block:: bash

      export OPENAI_API_KEY=your_key

3. Run an example:

   .. code-block:: bash

      cd examples
      python chatbots/search_bot.py

Example: Web Search Bot
-----------------------

.. code-block:: python

   # From examples/chatbots/search_bot.py
   import asyncio
   from pantheon.agent import Agent
   from magique.ai.tools.web_browse.duckduckgo import duckduckgo_search
   from magique.ai.tools.web_browse.web_crawl import web_crawl

   search_engine_expert = Agent(
       name="search_engine_expert",
       instructions="You are an expert in search engines. "
                   "You can search the web and crawl websites.",
       model="gpt-4o-mini",
       tools=[duckduckgo_search, web_crawl]
   )

   async def main():
       await search_engine_expert.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Example: Sequential Team
------------------------

.. code-block:: python

   # From examples/team/chat_with_seq_team.py
   import asyncio
   from pantheon.agent import Agent
   from pantheon.team import SequentialTeam

   scifi_fan = Agent(
       name="scifi_fan",
       instructions="You are a scifi fan. You like to read scifi books."
   )
   
   romance_fan = Agent(
       name="romance_fan",
       instructions="You are a romance fan. You like to read romance books."
   )

   team = SequentialTeam([scifi_fan, romance_fan])
   
   asyncio.run(team.chat("Recommend me some books."))

Example: Swarm Team
-------------------

.. code-block:: python

   # From examples/team/chat_with_swarm_team.py
   from pantheon.team import SwarmTeam
   from pantheon.repl.team import Repl

   # Create agents with transfer capabilities
   @scifi_fan.tool
   def transfer_to_romance_fan():
       return romance_fan

   @romance_fan.tool
   def transfer_to_scifi_fan():
       return scifi_fan

   team = SwarmTeam([scifi_fan, romance_fan])
   repl = Repl(team)
   await repl.run()

Learning Path
-------------

1. **Start Simple**: Try basic chatbots first
2. **Add Tools**: Experiment with search and code execution
3. **Team Patterns**: Explore different collaboration modes
4. **Build Applications**: Create your own specialized agents

Contributing Examples
---------------------

We welcome new examples! To contribute:

1. Create a clear, focused example
2. Include comments explaining key concepts
3. Test thoroughly
4. Submit via pull request

Next Steps
----------

- Read the :doc:`../guides/agents` guide
- Explore :doc:`../guides/teams` patterns
- Try the :doc:`../guides/chatroom` service