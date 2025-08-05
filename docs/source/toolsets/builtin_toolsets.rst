Built-in Toolsets
=================

Pantheon provides production-ready toolsets that can be deployed as services and connected to agents. Each toolset runs as an independent service with automatic tool registration and secure execution.

.. toctree::
   :maxdepth: 2
   
   python_interpreter
   r_interpreter
   shell
   web_browse
   scraper_api
   file_editor
   vector_rag

Overview
--------

All built-in toolsets:

- Run as independent services with unique service IDs
- Support both command-line and programmatic deployment
- Provide automatic tool discovery via ``@tool`` decorator
- Include security warnings and sandboxing where appropriate
- Can be run standalone or composed with other toolsets

Quick Start
-----------

Deploy a toolset from command line::

    # Run Python interpreter toolset
    python -m pantheon.toolsets.python --service-name python_tools
    
    # Run with custom parameters
    python -m pantheon.toolsets.python --service-name my_python --workdir /tmp

Connect from an agent::

    from pantheon.agent import Agent
    
    agent = Agent(name="coder", instructions="You can write Python code.")
    
    # Connect by service ID (shown when toolset starts)
    await agent.remote_toolset("abc123-service-id")
    
    # Or auto-discover by name
    await agent.remote_toolset(service_name="python_tools")

Deployment Patterns
-------------------

Using Context Manager
~~~~~~~~~~~~~~~~~~~~~

For development and testing::

    from pantheon.toolsets.python import PythonInterpreterToolSet
    from pantheon.toolsets.utils.toolset import run_toolsets
    
    async with run_toolsets([PythonInterpreterToolSet("python")]):
        # Toolset is running
        agent = Agent(name="assistant")
        await agent.remote_toolset(service_name="python")
        # Use agent with Python tools

Programmatic Deployment
~~~~~~~~~~~~~~~~~~~~~~~

For production environments::

    toolset = PythonInterpreterToolSet(
        name="secure_python",
        workdir="/sandbox",
        init_code="import numpy as np\nimport pandas as pd"
    )
    
    # Run as service
    await toolset.run(log_level="INFO")

Docker Deployment
~~~~~~~~~~~~~~~~~

Built-in toolsets have Docker images available::

    docker run -p 8080:8080 pantheon/python-toolset \
        --service-name python_sandbox \
        --workdir /workspace