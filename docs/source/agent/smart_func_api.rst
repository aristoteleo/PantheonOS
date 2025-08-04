Smart Function API
==================

The ``@smart_func`` decorator converts a function signature and docstring into an AI agent that implements the function's behavior. The function body can be empty (``pass``) as the implementation is handled by the LLM.

For simple LLM calls with fixed input/output types, smart functions provide a more convenient interface. However, for most use cases, you should prefer using the Agent API directly as it offers more control and flexibility.

Basic Usage
-----------

Simple Example
~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.smart_func import smart_func
   
   @smart_func(model="gpt-4.1-mini")
   async def summarize(text: str) -> str:
       """Summarize the given text into key points."""
       pass  # Implementation handled by LLM
   
   # Use as a regular async function
   summary = await summarize("Long article text here...")
   print(summary)

The decorator uses:
- The function's docstring as instructions for the agent
- The function's type hints to structure inputs and outputs
- The specified model to process requests

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

The ``@smart_func`` decorator accepts the following parameters:

.. code-block:: python

   @smart_func(
       func=None,                    # The function to decorate (automatic)
       model="gpt-4.1-nano",        # LLM model to use
       tools=None,                  # List of tool functions
       use_memory=False,            # Enable conversation memory
       memory=None,                 # Custom memory instance or initial messages
   )

Parameters:

* **model**: The LLM model to use. Defaults to "gpt-4.1-nano" for efficiency
* **tools**: List of functions the smart function can call during execution
* **use_memory**: Whether to maintain conversation context between calls
* **memory**: Either a Memory instance or a list of initial messages

Type Support
~~~~~~~~~~~~

Smart functions leverage Python type hints for structured I/O:

.. code-block:: python

   from pydantic import BaseModel
   from typing import List
   
   class Analysis(BaseModel):
       summary: str
       key_points: List[str]
       sentiment: str
       score: float
   
   @smart_func(model="gpt-4.1")
   async def analyze_feedback(feedback: str) -> Analysis:
       """Analyze customer feedback and return structured insights."""
       pass
   
   # Returns a properly typed Analysis instance
   result = await analyze_feedback("Great product, highly recommend!")
   print(f"Sentiment: {result.sentiment}, Score: {result.score}")

Synchronous Wrapper
~~~~~~~~~~~~~~~~~~~

Smart functions can be used in synchronous code:

.. code-block:: python

   @smart_func(model="gpt-4.1-mini")
   def classify_sync(text: str) -> str:
       """Classify the sentiment of the text."""
       pass
   
   # Synchronous function automatically runs async code
   result = classify_sync("I love this!")  # Works without await

Advanced Features
-----------------

Adding Tools
~~~~~~~~~~~~

Enhance smart functions with callable tools:

.. code-block:: python

   import requests
   from pantheon.smart_func import smart_func
   
   def fetch_weather(city: str) -> dict:
       """Fetch current weather data for a city."""
       response = requests.get(f"https://api.weather.com/{city}")
       return response.json()
   
   def calculate_comfort_index(temp: float, humidity: float) -> float:
       """Calculate comfort index from temperature and humidity."""
       return temp - 0.55 * (1 - humidity/100) * (temp - 14)
   
   @smart_func(
       model="gpt-4.1-mini",
       tools=[fetch_weather, calculate_comfort_index]
   )
   async def weather_advisor(city: str, activity: str) -> str:
       """Provide weather-based activity recommendations using current data."""
       pass
   
   advice = await weather_advisor("Tokyo", "jogging")

Memory Support
~~~~~~~~~~~~~~

Enable context retention across function calls:

.. code-block:: python

   from pantheon.smart_func import smart_func
   from pantheon.memory import Memory
   
   # With simple memory flag
   @smart_func(model="gpt-4.1-mini", use_memory=True)
   async def chat_assistant(message: str) -> str:
       """A helpful assistant that remembers the conversation."""
       pass
   
   # First call
   await chat_assistant("My name is Alice")
   
   # Second call - remembers context
   response = await chat_assistant("What's my name?")
   # Returns: "Your name is Alice"
   
   # With custom memory instance
   custom_memory = Memory("assistant_memory")
   
   @smart_func(
       model="gpt-4.1-mini",
       memory=custom_memory
   )
   async def persistent_assistant(query: str) -> str:
       """Assistant with persistent memory across sessions."""
       pass



Best Practices
--------------

Clear Docstrings
~~~~~~~~~~~~~~~~

The docstring becomes the agent's instructions, so be specific:

.. code-block:: python

   @smart_func(model="gpt-4.1-mini")
   async def good_example(text: str) -> str:
       """Extract all email addresses from the text.
       Return them as a comma-separated string.
       Handle invalid formats gracefully."""
       pass
   
   # Avoid vague instructions
   @smart_func(model="gpt-4.1-mini")
   async def bad_example(text: str) -> str:
       """Process the text."""  # Too vague
       pass

Type Annotations
~~~~~~~~~~~~~~~~

Always use type hints for better structure:

.. code-block:: python

   # Good - clear types
   @smart_func(model="gpt-4.1-mini")
   async def process(data: list[dict], threshold: float = 0.5) -> dict:
       """Process data items with threshold filtering."""
       pass
   
   # Avoid - ambiguous types
   async def process(data, threshold=None):
       pass


Tool Design
~~~~~~~~~~~

Keep tools focused and well-documented:

.. code-block:: python

   def fetch_data(query: str, limit: int = 10) -> list[dict]:
       """Fetch data from database matching query.
       
       Args:
           query: SQL-like query string
           limit: Maximum results to return
           
       Returns:
           List of matching records
       """
       # Implementation
   
   @smart_func(
       model="gpt-4.1-mini",
       tools=[fetch_data]
   )
   async def data_analyst(request: str) -> str:
       """Analyze data based on user request using database queries."""
       pass

Real-World Example
~~~~~~~~~~~~~~~~~~

From the paper reporter example:

.. code-block:: python

   from pantheon.smart_func import smart_func
   from pantheon.agent import Agent
   from pantheon.toolsets.web_browse import duckduckgo_search, web_crawl
   
   @smart_func(model="gpt-4.1-mini")
   async def extract_content(content: str) -> str:
       """Extract the most important content from the text. 
       For example, if the text is a paper, extract the
       authors, title, journal, publication date,
       abstract, introduction, methods, results, and discussion.
       """
       pass
   
   async def crawl_and_extract(urls: list[str]) -> list[str]:
       """Crawl provided urls and extract the most important content."""
       contents = await web_crawl(urls)
       extracted_contents = []
       
       for content in contents:
           try:
               extracted = await extract_content(content)
               extracted_contents.append(extracted)
           except Exception as e:
               print(f"Error: {e}")
               extracted_contents.append("")
       
       return extracted_contents
   
   # Use with an agent for more complex tasks
   search_agent = Agent(
       name="Search Agent",
       instructions="You are a search engine expert.",
       model="gpt-4.1",
       tools=[duckduckgo_search, crawl_and_extract]
   )

When to Use Smart Functions
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use smart functions when:

* You need simple, fixed input/output transformations
* The task can be clearly defined in a docstring
* You want minimal boilerplate for LLM calls

Use the Agent API directly when:

* You need fine-grained control over the conversation
* You want to manage complex multi-turn interactions
* You need custom error handling or response processing
* You're building production systems requiring flexibility