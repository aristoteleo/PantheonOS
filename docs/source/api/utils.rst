Utils Module
============

.. module:: pantheon.utils

The utils module provides utility functions and helpers used throughout Pantheon.

Submodules
----------

LLM Utilities
~~~~~~~~~~~~~

.. automodule:: pantheon.utils.llm
   :members:
   :undoc-members:

Vision Utilities
~~~~~~~~~~~~~~~~

.. automodule:: pantheon.utils.vision
   :members:
   :undoc-members:

Logging Utilities
~~~~~~~~~~~~~~~~~

.. automodule:: pantheon.utils.log
   :members:
   :undoc-members:

Miscellaneous Utilities
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: pantheon.utils.misc
   :members:
   :undoc-members:

Overview
--------

The utils module contains helper functions for:

- LLM interaction and message processing
- Vision/image handling
- Logging configuration
- General utility functions

LLM Utilities
-------------

The `llm` module provides functions for interacting with language models.

Common Functions
~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.utils.llm import (
       acompletion_openai,
       acompletion,  # adapter-based completion
       process_messages_for_model,
       remove_hidden_fields
   )

   # Process messages for specific model
   messages = process_messages_for_model(
       messages=[
           {"role": "user", "content": "Hello"},
           {"role": "assistant", "content": "Hi there!"}
       ],
       model="gpt-4"
   )

   # Make completion request
   response = await acompletion_openai(
       model="gpt-4",
       messages=messages,
       temperature=0.7
   )

Message Processing
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Remove hidden fields from messages
   clean_messages = remove_hidden_fields(messages)

   # Process for specific hooks
   hook_messages = process_messages_for_hook_func(
       messages,
       hook_function
   )

Vision Utilities
----------------

The `vision` module handles image inputs for agents.

VisionInput Class
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.utils.vision import VisionInput, vision_to_openai

   # Create vision input
   vision_input = VisionInput(
       text="What's in this image?",
       images=["path/to/image.jpg", "path/to/image2.png"]
   )

   # Convert to OpenAI format
   openai_messages = vision_to_openai(vision_input)

Image Handling
~~~~~~~~~~~~~~

.. code-block:: python

   # Agent with vision support
   agent = Agent(
       name="vision_agent",
       instructions="Analyze images and answer questions"
   )

   # Use with vision input
   result = await agent.run(
       VisionInput(
           text="Describe these images",
           images=["image1.jpg", "image2.jpg"]
       )
   )

Logging Configuration
---------------------

The `log` module provides consistent logging across Pantheon.

Logger Setup
~~~~~~~~~~~~

.. code-block:: python

   from pantheon.utils.log import logger

   # Use the pre-configured logger
   logger.info("Starting agent")
   logger.debug("Debug information")
   logger.error("Error occurred", exc_info=True)

Custom Logging
~~~~~~~~~~~~~~

.. code-block:: python

   import logging
   from pantheon.utils.log import setup_logging

   # Custom logging configuration
   setup_logging(
       level=logging.DEBUG,
       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
       log_file='pantheon.log'
   )

Miscellaneous Utilities
-----------------------

Helper Functions
~~~~~~~~~~~~~~~~

The `misc` module contains various helper functions:

.. code-block:: python

   from pantheon.utils.misc import (
       desc_to_openai_dict,
       run_func
   )

   # Convert function description to OpenAI format
   func_desc = parse_func(my_function)
   openai_dict = desc_to_openai_dict(func_desc)

   # Run function with timeout
   result = await run_func(
       async_function,
       timeout=30,
       *args,
       **kwargs
   )

Utility Examples
----------------

Rate Limiting
~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.utils.misc import RateLimiter

   # Create rate limiter
   limiter = RateLimiter(
       max_calls=100,
       period=60  # 100 calls per minute
   )

   # Use with agent
   @limiter.limit
   async def make_api_call():
       return await agent.run("Task")

Retry Logic
~~~~~~~~~~~

.. code-block:: python

   from pantheon.utils.misc import retry_with_backoff

   @retry_with_backoff(
       max_retries=3,
       initial_delay=1.0,
       backoff_factor=2.0
   )
   async def unreliable_operation():
       # Operation that might fail
       pass

Token Counting
~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.utils.llm import count_tokens

   # Count tokens in messages
   messages = [
       {"role": "user", "content": "Hello, how are you?"},
       {"role": "assistant", "content": "I'm doing well!"}
   ]

   token_count = count_tokens(messages, model="gpt-4")
   print(f"Total tokens: {token_count}")

Best Practices
--------------

1. **Use Provided Utilities**: Leverage existing utilities instead of reimplementing
2. **Consistent Logging**: Use the configured logger for all logging
3. **Error Handling**: Utility functions include proper error handling
4. **Type Safety**: Utilities use type hints for better IDE support
5. **Performance**: Utilities are optimized for common use cases

Integration with Agents
-----------------------

Utilities in Agent Creation
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.utils.log import logger
   from pantheon.utils.vision import VisionInput

   class CustomAgent(Agent):
       async def run(self, input_data):
           # Use logger
           logger.info(f"Processing input: {type(input_data)}")
           
           # Handle vision input
           if isinstance(input_data, VisionInput):
               logger.debug("Processing vision input")
               messages = vision_to_openai(input_data)
           else:
               messages = [{"role": "user", "content": str(input_data)}]
           
           # Process with utilities
           return await super().run(messages)

Environment Variables
---------------------

Utilities respect environment variables:

.. code-block:: bash

   # Logging level
   export PANTHEON_LOG_LEVEL=DEBUG

   # API timeouts
   export PANTHEON_API_TIMEOUT=30

   # Token limits
   export PANTHEON_MAX_TOKENS=4096

Future Enhancements
-------------------

Planned utility additions:

- Caching utilities
- Serialization helpers
- Validation functions
- Performance profiling
- Metrics collection