Installation & Setup
====================

This guide will help you install Pantheon and set up your environment.

Requirements
------------

- Python 3.10 or higher
- API keys for LLM providers (e.g., OpenAI, Anthropic)

Using uv (Recommended)
----------------------

`uv <https://github.com/astral-sh/uv>`_ is a fast Python package manager that handles dependencies efficiently.

Install uv
~~~~~~~~~~

.. code-block:: bash

   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Windows
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

Clone and Install
~~~~~~~~~~~~~~~~~

.. code-block:: bash

   git clone https://github.com/aristoteleo/PantheonOS.git
   cd PantheonOS
   uv sync

With Optional Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # RAG/vector search support
   uv sync --extra knowledge

   # PantheonClaw mobile gateway channels
   uv sync --extra claw

   # R language support (requires R installed on system)
   uv sync --extra r

   # Multiple extras
   uv sync --extra knowledge --extra claw

Using pip
---------

Install from PyPI
~~~~~~~~~~~~~~~~~

.. code-block:: bash

   pip install pantheon-agents

With Optional Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # RAG/vector search support (LlamaIndex, Qdrant)
   pip install "pantheon-agents[knowledge]"

   # PantheonClaw mobile gateway channels
   pip install "pantheon-agents[claw]"

   # Multiple extras
   pip install "pantheon-agents[knowledge,claw]"

Install from Source
~~~~~~~~~~~~~~~~~~~

For the latest development version:

.. code-block:: bash

   git clone https://github.com/aristoteleo/PantheonOS.git
   cd PantheonOS
   pip install -e ".[knowledge]"

Development Installation
------------------------

For contributors and developers:

.. code-block:: bash

   git clone https://github.com/aristoteleo/PantheonOS.git
   cd PantheonOS

   # Using uv (recommended)
   uv sync --extra dev --extra knowledge

   # Run tests
   uv run pytest tests/

   # Or using pip
   pip install -e ".[dev,knowledge]"
   pytest tests/

Optional Dependencies
---------------------

Pantheon provides several optional dependency groups:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Extra
     - Description
   * - ``knowledge``
     - RAG and vector search support (LlamaIndex, Qdrant, fastembed)
   * - ``slack``
     - Slack bot integration (slack-sdk, slack-bolt)
   * - ``r``
     - R language support for notebooks (rpy2, requires R installed)
   * - ``dev``
     - Development and testing tools (pytest, pytest-asyncio)

Core Dependencies
-----------------

Core dependencies are automatically installed:

- **Provider Adapters** - Unified LLM API access (OpenAI, Anthropic, etc.)
- **Rich** - Terminal UI and formatting
- **prompt-toolkit** - Interactive REPL
- **NATS** - Distributed messaging
- **FastMCP** - Model Context Protocol support
- **LanceDB** - Vector database for embeddings
- **Crawl4AI** - Web content extraction

Environment Setup
-----------------

API Keys
~~~~~~~~

Set up your LLM provider API keys:

.. code-block:: bash

   # OpenAI (default)
   export OPENAI_API_KEY="your-openai-key"

   # Anthropic Claude
   export ANTHROPIC_API_KEY="your-anthropic-key"

   # Or use other supported providers
   export GEMINI_API_KEY="your-gemini-key"

You can also create a ``.env`` file in your project directory:

.. code-block:: bash

   # .env
   OPENAI_API_KEY=your-openai-key
   ANTHROPIC_API_KEY=your-anthropic-key

Custom API Endpoint (Third-party Proxy)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you use a third-party API proxy service, set these two universal variables
to route all LLM calls through your proxy:

.. code-block:: bash

   export LLM_API_BASE="https://your-proxy.com/v1"
   export LLM_API_KEY="your-proxy-key"

You can also configure this interactively:

.. code-block:: bash

   # Launch the setup wizard at any time
   pantheon setup

   # Or use /keys in the REPL
   /keys 0 https://your-proxy.com/v1 your-proxy-key

**Priority**: Provider-specific keys (e.g. ``OPENAI_API_KEY``) take precedence
over universal keys. When ``LLM_API_BASE`` is active, ``LLM_API_KEY`` is preferred
over provider-specific keys.

Configuration Directory
~~~~~~~~~~~~~~~~~~~~~~~

Pantheon uses a layered configuration system:

1. **User global config**: ``~/.pantheon/``
2. **Project config**: ``./.pantheon/``

Create a project configuration:

.. code-block:: bash

   mkdir .pantheon
   # Configuration files will be auto-generated on first run

Starting the REPL
-----------------

The easiest way to start using Pantheon:

.. code-block:: bash

   # With uv
   pantheon cli

   # With pip installation
   pantheon cli

This launches the interactive REPL with default settings.

Verifying Installation
----------------------

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent

   async def main():
       agent = Agent(
           name="test",
           instructions="Say hello!"
       )
       response = await agent.run("Hello!")
       print(response.content)

   asyncio.run(main())

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**ImportError: No module named 'pantheon'**
   Ensure you've activated your virtual environment or installed the package correctly.

**API key errors**
   Make sure your API keys are set as environment variables or in a ``.env`` file.

**NATS connection errors**
   For distributed features, ensure NATS server is running. For local development,
   Pantheon can auto-start a NATS server if ``nats-server-bin`` is installed.

Next Steps
----------

- :doc:`first-steps` - Create your first agent
- :doc:`/concepts` - Learn core concepts
- :doc:`/examples/index` - Explore example implementations
