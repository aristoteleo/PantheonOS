RAG System (Auto-Build)
=======================

The RAG System provides tools for automatically building vector databases from various sources including documentation websites, GitHub repositories, and local files. It features web crawling, document processing, and integration with Hugging Face for database distribution.

Overview
--------

The RAG auto-build system (``pantheon.toolsets.utils.rag``) provides:

- **Automated Web Crawling**: Deep crawl documentation sites and extract content
- **Multi-Source Support**: Build from websites, GitHub READMEs, and local files
- **Vector Database Creation**: Automatic chunking and embedding with LanceDB
- **Hugging Face Integration**: Upload and download pre-built databases
- **Caching System**: Intelligent caching for embeddings and build progress
- **YAML Configuration**: Define sources and parameters in YAML files

Command Line Usage
------------------

Build from YAML Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a YAML file defining your sources::

    # rag_config.yaml
    python_docs:
      type: vector_db
      parameters:
        embedding_model: text-embedding-3-large
        chunk_size: 4000
        chunk_overlap: 200
      items:
        python_official:
          type: package documentation
          url: https://docs.python.org/3/
        numpy_docs:
          type: package documentation  
          url: https://numpy.org/doc/stable/
        pandas_github:
          type: github readme
          url: https://raw.githubusercontent.com/pandas-dev/pandas/main/README.md

Build the database::

    python -m pantheon.toolsets.utils.rag build rag_config.yaml ./output_dir

Upload to Hugging Face
~~~~~~~~~~~~~~~~~~~~~~

Share your built database::

    # Set your Hugging Face token
    export HUGGINGFACE_TOKEN=your_token_here
    
    # Upload to default repo (NaNg/pantheon_rag_db)
    python -m pantheon.toolsets.utils.rag upload ./output_dir
    
    # Or specify custom repo
    python -m pantheon.toolsets.utils.rag upload ./output_dir --repo-id your-username/your-repo

Download Pre-built Database
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Download existing databases::

    # Download from default repo
    python -m pantheon.toolsets.utils.rag download ./local_dir
    
    # Download from custom repo
    python -m pantheon.toolsets.utils.rag download ./local_dir --repo-id username/repo --filename custom.zip

Programmatic Usage
------------------

Using VectorDB Class
~~~~~~~~~~~~~~~~~~~~

The ``VectorDB`` class provides direct access to vector database operations::

    from pantheon.toolsets.utils.rag.vectordb import VectorDB
    
    # Load existing database
    db = VectorDB("./output_dir/python_docs")
    
    # Query the database
    results = await db.query(
        query="How to use async functions in Python?",
        top_k=5,
        source="python_official"  # Optional: filter by source
    )
    
    # Insert new content
    await db.insert(
        text="Your new content here",
        metadata={"source": "custom", "date": "2024-01-01"}
    )
    
    # Insert from file with automatic chunking
    await db.insert_from_file(
        file_path="./new_doc.md",
        metadata={"source": "local_docs"}
    )

Building Programmatically
~~~~~~~~~~~~~~~~~~~~~~~~~

Build databases from Python code::

    from pantheon.toolsets.utils.rag.build import build_vector_db
    
    db_config = {
        "type": "vector_db",
        "parameters": {
            "embedding_model": "text-embedding-3-large",
            "chunk_size": 4000,
            "chunk_overlap": 200
        },
        "items": {
            "my_docs": {
                "type": "package documentation",
                "url": "https://mydocs.example.com"
            }
        }
    }
    
    await build_vector_db("my_knowledge_base", db_config, "./output")

Architecture Details
--------------------

Document Processing
~~~~~~~~~~~~~~~~~~~

The system supports three types of sources:

1. **Package Documentation**: Deep crawls documentation websites
2. **GitHub README**: Fetches README files from GitHub repositories  
3. **Tutorial/Local Files**: Processes local markdown files

Web Crawling
~~~~~~~~~~~~

Uses ``crawl4ai`` for intelligent web crawling::

    - BFS (Breadth-First Search) strategy for deep crawling
    - Configurable max depth and external link inclusion
    - Automatic markdown extraction from HTML
    - Duplicate detection via content hashing

Text Processing
~~~~~~~~~~~~~~~

Smart text splitting with configurable parameters::

    - Default chunk size: 4000 characters
    - Default overlap: 200 characters
    - Preserves context across chunks
    - Handles markdown formatting

Embedding and Storage
~~~~~~~~~~~~~~~~~~~~~

Efficient vector storage with LanceDB::

    - Supports multiple embedding models (OpenAI text-embedding-3-*)
    - Disk-based caching for embeddings
    - PyArrow schema for structured storage
    - Metadata tracking for each chunk

Key Features
------------

Progress Tracking
~~~~~~~~~~~~~~~~~

The build system maintains progress state::

    {
        "item_name": {
            "success": true,
            "created_at": "2024-01-01T12:00:00",
            "download_success": true
        }
    }

Failed items can be retried without re-processing successful ones.

Caching System
~~~~~~~~~~~~~~

Two-level caching for efficiency:

1. **Embedding Cache**: Prevents redundant API calls
2. **Build Cache**: Tracks processing status per source

Source Types
~~~~~~~~~~~~

Supported source types in YAML configuration:

- ``package documentation``: For documentation websites
- ``github readme``: For GitHub repository READMEs
- ``tutorial``: For collections of tutorial pages

Example: Multi-Source Knowledge Base
------------------------------------

Create a comprehensive knowledge base::

    # ml_knowledge.yaml
    ml_frameworks:
      type: vector_db
      parameters:
        embedding_model: text-embedding-3-large
        chunk_size: 3000
        chunk_overlap: 300
      items:
        pytorch_docs:
          type: package documentation
          url: https://pytorch.org/docs/stable/
        tensorflow_docs:
          type: package documentation
          url: https://www.tensorflow.org/api_docs
        scikit_learn:
          type: package documentation
          url: https://scikit-learn.org/stable/
        transformers_github:
          type: github readme
          url: https://raw.githubusercontent.com/huggingface/transformers/main/README.md

Build and use::

    # Build the database
    python -m pantheon.toolsets.utils.rag build ml_knowledge.yaml ./ml_db
    
    # Use in your application
    from pantheon.toolsets.utils.rag.vectordb import VectorDB
    
    db = VectorDB("./ml_db/ml_frameworks")
    results = await db.query("How to fine-tune BERT?", top_k=10)

Integration with Vector RAG Toolset
-----------------------------------

Using with Agents
~~~~~~~~~~~~~~~~~

Combine the auto-built database with the Vector RAG toolset::

    from pantheon.toolsets.vector_rag import VectorRAGToolSet
    from pantheon.agent import Agent
    
    # Point to your built database
    rag_toolset = VectorRAGToolSet(
        name="ml_assistant",
        db_path="./ml_db/ml_frameworks"
    )
    
    # Create agent with RAG capabilities
    agent = Agent(
        name="ml_expert",
        instructions="You are an ML expert. Use the knowledge base to answer questions.",
        model="gpt-4.1"
    )
    
    # Connect to toolset
    await agent.remote_toolset(rag_toolset.service_id)

Best Practices
--------------

1. **Choose Appropriate Chunk Sizes**: 
   - Larger chunks (3000-5000) for narrative content
   - Smaller chunks (500-1500) for technical references

2. **Select Embedding Models Wisely**:
   - ``text-embedding-3-large``: Best quality, higher cost
   - ``text-embedding-3-small``: Good balance for most use cases

3. **Organize Sources Logically**: Group related documentation in the same database

4. **Monitor Build Progress**: Check ``info_cache.json`` for build status

5. **Use Hugging Face for Distribution**: Share pre-built databases to save compute

6. **Regular Updates**: Rebuild databases periodically for fresh content

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Build Failures**::

    - Check network connectivity for web crawling
    - Verify URLs are accessible
    - Review error messages in info_cache.json

**Embedding Errors**::

    - Ensure OPENAI_API_KEY is set
    - Check API rate limits
    - Verify model availability

**Storage Issues**::

    - Ensure sufficient disk space
    - Check write permissions
    - Clean old cache files if needed

Performance Tips
~~~~~~~~~~~~~~~~

- Use embedding cache to avoid redundant API calls
- Enable progress tracking to resume interrupted builds
- Process multiple sources in parallel when possible
- Consider using smaller embedding models for large datasets