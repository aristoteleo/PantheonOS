Vector RAG
==========

The Vector RAG toolset provides semantic search capabilities using LanceDB as the vector database backend. It enables agents to query and manage vector databases for retrieval-augmented generation tasks.

Overview
--------

The VectorRAGToolSet is part of ``pantheon-toolsets`` and provides:

* **LanceDB Integration**: Uses LanceDB for efficient vector storage and retrieval
* **Semantic Search**: Query documents by meaning using vector embeddings
* **Database Management**: Get information about stored vectors and collections
* **Optional Write Operations**: Insert and delete capabilities (disabled by default for safety)
* **Service-Based Architecture**: Runs as an independent service with automatic tool registration

Available Tools
---------------

The VectorRAGToolSet provides these tools:

**query_vector_db**
    Search the vector database using semantic similarity
    
    * ``query``: The search query text
    * ``db_name``: Database name to query
    * ``top_k``: Number of results to return (default: 5)
    * ``filter``: Optional metadata filter

**get_vector_db_info**
    Get information about available vector databases
    
    * ``db_name``: Optional specific database name
    * Returns: Database statistics, schema, and metadata

**insert_to_vector_db** (Optional)
    Insert new documents into the vector database
    
    * ``documents``: List of documents to insert
    * ``db_name``: Target database name
    * Note: Only available when ``write_enabled=True``

**delete_from_vector_db** (Optional)
    Delete documents from the vector database
    
    * ``ids``: List of document IDs to delete
    * ``db_name``: Target database name
    * Note: Only available when ``write_enabled=True``

Basic Usage
-----------

Command Line Deployment
~~~~~~~~~~~~~~~~~~~~~~~

Run the Vector RAG toolset as a service::

    # Basic deployment (read-only mode)
    python -m pantheon.toolsets.vector_rag --service-name vector_rag
    
    # Enable write operations
    python -m pantheon.toolsets.vector_rag --service-name vector_rag --write-enabled
    
    # Custom database directory
    python -m pantheon.toolsets.vector_rag --service-name vector_rag --db-dir /path/to/databases

Programmatic Deployment
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets.vector_rag import VectorRAGToolSet
   from pantheon.agent import Agent
   
   # Create Vector RAG toolset
   vector_tools = VectorRAGToolSet(
       name="knowledge_base",
       db_directory="./vector_dbs",
       write_enabled=False  # Read-only by default
   )
   
   # Run as service
   await vector_tools.run()
   
   # Connect agent to the service
   rag_agent = Agent(
       name="knowledge_assistant",
       instructions="Answer questions using the vector knowledge base.",
       model="gpt-4.1"
   )
   await rag_agent.remote_toolset(service_name="knowledge_base")

Example Usage
-------------

Querying Vector Database
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Agent searches for relevant information
   response = await rag_agent.run([{
       "role": "user",
       "content": "Find information about transformer architectures in the ML database"
   }])
   
   # The agent will use query_vector_db tool to:
   # 1. Search the specified database
   # 2. Return top-k most similar documents
   # 3. Include metadata and similarity scores

Database Information
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Get information about available databases
   response = await rag_agent.run([{
       "role": "user",
       "content": "What vector databases are available and what do they contain?"
   }])
   
   # Returns database schemas, document counts, and metadata

Write Operations (When Enabled)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Deploy with write access
   vector_tools = VectorRAGToolSet(
       name="writable_kb",
       write_enabled=True
   )
   
   # Insert documents
   response = await agent.run([{
       "role": "user",
       "content": "Insert this research paper about GPT-4 into the ML database"
   }])
   
   # Delete documents
   response = await agent.run([{
       "role": "user",
       "content": "Remove document with ID 'doc-123' from the database"
   }])

Integration with RAG System
---------------------------

The Vector RAG toolset is designed to work seamlessly with the :doc:`RAG System <rag_system>` for automatic database building:

.. code-block:: python

   from pantheon.toolsets.utils.rag.build import build_vector_db
   
   # Build vector database from documentation
   await build_vector_db(
       docs_url="https://docs.example.com",
       output_dir="./vector_dbs/docs",
       db_name="documentation"
   )
   
   # Query the built database
   vector_tools = VectorRAGToolSet(
       name="docs_rag",
       db_directory="./vector_dbs"
   )
   
   agent = Agent(
       name="docs_assistant",
       instructions="Answer questions using the documentation database."
   )
   await agent.remote_toolset(service_name="docs_rag")

Configuration Options
---------------------

Constructor Parameters
~~~~~~~~~~~~~~~~~~~~~~

* ``name``: Service name for the toolset
* ``db_directory``: Directory containing LanceDB databases (default: "./lancedb")
* ``write_enabled``: Enable insert/delete operations (default: False)
* ``worker_params``: Additional parameters for the NATS worker
* ``endpoint_service_id``: Optional specific service ID

Command Line Arguments
~~~~~~~~~~~~~~~~~~~~~~

When running via command line::

    python -m pantheon.toolsets.vector_rag [options]
    
    Options:
      --service-name NAME     Name for the service (default: vector-rag)
      --db-dir PATH          Directory for databases (default: ./lancedb)
      --write-enabled        Enable write operations
      --log-level LEVEL      Logging level (default: INFO)

Best Practices
--------------

1. **Security**: Keep ``write_enabled=False`` unless insertion/deletion is required
2. **Database Organization**: Use separate databases for different domains or projects
3. **Query Optimization**: Use appropriate ``top_k`` values to balance performance and accuracy
4. **Metadata**: Include rich metadata when building databases for better filtering
5. **Service Deployment**: Run as a dedicated service for production use
6. **Database Location**: Store databases in persistent volumes for production deployments

Example: Knowledge Base Assistant
---------------------------------

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets.vector_rag import VectorRAGToolSet
   from pantheon.toolsets.utils.toolset import run_toolsets
   
   async def create_knowledge_assistant():
       # Deploy Vector RAG service
       vector_tools = VectorRAGToolSet(
           name="company_kb",
           db_directory="./company_databases"
       )
       
       async with run_toolsets([vector_tools]):
           # Create knowledge assistant
           assistant = Agent(
               name="kb_assistant",
               instructions="""
               You are a knowledge base assistant. You can:
               1. Search the company knowledge base for information
               2. Provide accurate answers with sources
               3. Explain what databases are available
               
               Always cite the source documents when providing information.
               """,
               model="gpt-4.1"
           )
           
           # Connect to vector database service
           await assistant.remote_toolset(service_name="company_kb")
           
           # Use the assistant
           response = await assistant.run([{
               "role": "user",
               "content": "What are our company's remote work policies?"
           }])
           
           print(response.messages[-1].content)

See Also
--------

* :doc:`rag_system` - Automatic vector database building from documentation
* :doc:`custom_toolsets` - Creating custom toolsets