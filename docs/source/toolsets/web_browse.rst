Web Browse
==========

The Web Browse toolset provides web search and content retrieval capabilities through the ``WebBrowseToolSet`` class. It includes DuckDuckGo search and web crawling functionality.

Overview
--------

The ``WebBrowseToolSet`` provides two main tools:

- **duckduckgo_search**: Search the web using DuckDuckGo
- **web_crawl**: Fetch and extract content from URLs

Both tools run in thread mode for efficient I/O operations.

Available as Standalone Functions
---------------------------------

The web browse tools can also be used directly without a toolset::

    from pantheon.toolsets.web_browse import duckduckgo_search, web_crawl
    from pantheon.agent import Agent
    
    # Add as individual tools to an agent
    agent = Agent(
        name="researcher",
        instructions="You can search the web and analyze content.",
        tools=[duckduckgo_search, web_crawl]
    )

Running as a Service
--------------------

Deploy the toolset as a service::

    # Command line
    python -m pantheon.toolsets.web_browse --service-name web_tools
    
    # Programmatic
    from pantheon.toolsets.web_browse import WebBrowseToolSet
    
    toolset = WebBrowseToolSet("web_browse")
    await toolset.run()

Available Tools
---------------

duckduckgo_search
~~~~~~~~~~~~~~~~~

Search the web using DuckDuckGo::

    Parameters:
        query (str): The search query
        max_results (int): Maximum number of results (default: 10)
        time_limit (str | None): Time limit - "d" (day), "w" (week), "m" (month), "y" (year)
    
    Returns:
        List of search results with title, href, and body

web_crawl
~~~~~~~~~

Fetch and extract content from URLs::

    Parameters:
        urls (list[str]): List of URLs to crawl
        timeout (float): Request timeout in seconds (default: 20.0)
    
    Returns:
        List of extracted content from each URL

Advanced Features
-----------------

Multi-Page Research
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   research_agent = Agent(
       name="deep_researcher",
       instructions="""Conduct thorough research:
       1. Search for multiple sources
       2. Visit each relevant link
       3. Extract and synthesize information
       4. Provide comprehensive analysis""",
       tools=[duckduckgo_search, web_crawl]
   )
   
   response = await research_agent.run([{
       "role": "user",
       "content": "Research the impact of AI on healthcare with at least 5 sources"
   }])

Content Extraction
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   extractor_agent = Agent(
       name="content_extractor",
       instructions="Extract specific information from web pages.",
       tools=[web_crawl]
   )
   
   # Extract structured data
   response = await extractor_agent.run([{
       "role": "user",
       "content": """From this product page, extract:
       - Product name
       - Price
       - Features
       - Customer reviews summary
       URL: https://example.com/product"""
   }])

Link Following
~~~~~~~~~~~~~~

.. code-block:: python

   crawler_agent = Agent(
       name="web_crawler",
       instructions="Navigate through websites by following relevant links.",
       tools=[web_crawl]
   )
   
   # Crawl with depth
   response = await crawler_agent.run([{
       "role": "user",
       "content": "Starting from the homepage, find all documentation pages"
   }])

Common Patterns
---------------

News Monitoring
~~~~~~~~~~~~~~~

.. code-block:: python

   news_agent = Agent(
       name="news_monitor",
       instructions="""Monitor news on specific topics:
       1. Search for recent news
       2. Filter by date and relevance
       3. Summarize key developments
       4. Identify trends""",
       tools=[duckduckgo_search, web_crawl]
   )
   
   # Monitor specific topic
   response = await news_agent.run([{
       "role": "user",
       "content": "Find news about renewable energy from the past week"
   }])

Competitive Analysis
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   competitor_agent = Agent(
       name="competitor_analyst",
       instructions="""Analyze competitor websites:
       1. Find competitor sites
       2. Extract product information
       3. Compare features and pricing
       4. Identify market positioning""",
       tools=[duckduckgo_search, web_crawl]
   )

Fact Checking
~~~~~~~~~~~~~

.. code-block:: python

   fact_checker = Agent(
       name="fact_checker",
       instructions="""Verify claims by:
       1. Searching multiple sources
       2. Finding authoritative references
       3. Cross-referencing information
       4. Providing confidence assessment""",
       tools=[duckduckgo_search, web_crawl]
   )
   
   response = await fact_checker.run([{
       "role": "user",
       "content": "Verify: The Great Wall of China is visible from space"
   }])

Integration Patterns
--------------------

With Data Analysis
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Combine web data with analysis
   market_analyst = Agent(
       name="market_analyst",
       instructions="Gather market data from web and analyze trends.",
       tools=[duckduckgo_search, web_crawl]
   )
   await market_analyst.remote_toolset(python_tools.service_id)
   
   # Agent workflow:
   # 1. Search for market data sources
   # 2. Fetch data from multiple sites
   # 3. Use Python to analyze trends
   # 4. Create visualizations

With Content Creation
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   content_researcher = Agent(
       name="content_researcher",
       instructions="""Research topics and create content:
       1. Search for authoritative sources
       2. Gather diverse perspectives
       3. Extract key information
       4. Create original content""",
       tools=[duckduckgo_search, web_crawl, write_file]
   )

Best Practices
--------------

1. **Rate Limiting**: Respect website rate limits
2. **Error Handling**: Handle network errors gracefully
3. **Content Validation**: Verify extracted information
4. **Source Attribution**: Always cite sources
5. **Privacy**: Respect robots.txt and privacy policies

Error Handling
--------------

Network Errors
~~~~~~~~~~~~~~

.. code-block:: python

   class RobustWebAgent(Agent):
       async def fetch_with_retry(self, url: str, max_retries: int = 3):
           """Fetch URL with retry logic."""
           for attempt in range(max_retries):
               try:
                   response = await self.run([{
                       "role": "user",
                       "content": f"Fetch content from {url}"
                   }])
                   return response
               except Exception as e:
                   if attempt == max_retries - 1:
                       # Try alternative approach
                       return await self.search_cache(url)
                   await asyncio.sleep(2 ** attempt)

Content Parsing
~~~~~~~~~~~~~~~

.. code-block:: python

   class SmartExtractor(Agent):
       async def extract_safely(self, url: str, selectors: List[str]):
           """Extract content with fallbacks."""
           try:
               # Try primary extraction
               content = await self.extract_with_selectors(url, selectors)
           except:
               # Fallback to general extraction
               content = await self.extract_general(url)
           
           return self.validate_content(content)

Advanced Usage
--------------

Custom Search Engines
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class MultiSearchAgent(Agent):
       async def search_multiple(self, query: str):
           """Search across multiple search engines."""
           results = {}
           
           # DuckDuckGo
           results['duckduckgo'] = await self.run([{
               "role": "user",
               "content": f"Search DuckDuckGo for: {query}"
           }])
           
           # Could add other search APIs
           # results['bing'] = await self.search_bing(query)
           # results['google'] = await self.search_google(query)
           
           # Merge and deduplicate results
           return self.merge_results(results)

Semantic Search
~~~~~~~~~~~~~~~

.. code-block:: python

   semantic_searcher = Agent(
       name="semantic_searcher",
       instructions="""Perform semantic search:
       1. Understand search intent
       2. Generate multiple search queries
       3. Filter results by relevance
       4. Extract semantic meaning""",
       tools=[duckduckgo_search, web_crawl]
   )

Web Monitoring
~~~~~~~~~~~~~~

.. code-block:: python

   monitor_agent = Agent(
       name="web_monitor",
       instructions="""Monitor web pages for changes:
       1. Fetch current content
       2. Compare with previous version
       3. Identify significant changes
       4. Alert on important updates""",
       tools=[web_crawl]
   )

Performance Tips
----------------

- Cache frequently accessed pages
- Batch multiple searches when possible
- Use appropriate timeouts
- Extract only necessary content
- Implement progressive loading for large sites

Limitations
-----------

- JavaScript-heavy sites may not render fully
- Some sites block automated access
- Rate limits apply to search engines
- Cannot handle authentication-required pages
- Limited to publicly accessible content

Security Considerations
-----------------------

- Validate URLs before fetching
- Sanitize extracted content
- Respect robots.txt
- Implement user-agent headers
- Handle malicious content safely