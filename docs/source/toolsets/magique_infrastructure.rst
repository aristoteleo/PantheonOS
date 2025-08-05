Magique Infrastructure
======================

Pantheon's toolset system is built on top of Magique, a WebSocket-based message transfer framework that enables distributed communication between agents and toolsets.

Overview
--------

Magique provides the underlying communication infrastructure for Pantheon's distributed architecture:

- **Message Transfer**: WebSocket-based protocol for real-time communication
- **Service Registry**: Central registry for discovering and connecting to services
- **Load Distribution**: Support for multiple servers to distribute load
- **Authentication**: Optional JWT-based authentication for secure connections

Architecture Components
-----------------------

Magique Client
~~~~~~~~~~~~~~

The client component enables agents to discover and invoke remote services. Each agent maintains a persistent WebSocket connection to the Magique server for real-time communication.

Magique Worker
~~~~~~~~~~~~~~

Workers register services (toolsets) with the Magique server and handle incoming function calls. Each toolset runs as a worker that:

- Registers its available functions with the server
- Processes incoming requests asynchronously  
- Returns results through the server to clients

Magique Server
~~~~~~~~~~~~~~

The server acts as a message broker and service registry, routing requests between clients and workers.

Running a Magique Server
------------------------

Starting a Server
~~~~~~~~~~~~~~~~~

To run your own Magique server::

    # Install Magique
    pip install magique
    
    # Run server with default settings (port 8765)
    python -m magique.server
    
    # Run with custom host and port
    python -m magique.server --host 0.0.0.0 --port 8080
    
    # Run with specific log level
    python -m magique.server --log-level INFO

The server will start and display::

    INFO: Uvicorn running on http://0.0.0.0:8765
    INFO: Started server process

Configuring Services to Use Custom Servers
------------------------------------------

By default, Pantheon toolsets connect to the official Magique servers. You can configure services to use your own servers through the ``MAGIQUE_SERVER_URL`` environment variable.

Single Server
~~~~~~~~~~~~~

To use a single custom server::

    export MAGIQUE_SERVER_URL="ws://localhost:8765/ws"
    python -m pantheon.toolsets.python

Multiple Servers
~~~~~~~~~~~~~~~~

For load distribution across multiple servers, separate URLs with ``|``::

    export MAGIQUE_SERVER_URL="ws://server1:8765/ws|ws://server2:8765/ws|ws://server3:8765/ws"
    python -m pantheon.toolsets.python

The toolset will randomly select one of the available servers for connection.

Secure Connections
~~~~~~~~~~~~~~~~~~

For production use with SSL/TLS::

    export MAGIQUE_SERVER_URL="wss://secure-server.example.com/ws"

Default Configuration
---------------------

If ``MAGIQUE_SERVER_URL`` is not set, Pantheon uses the following default servers::

    wss://magique1.aristoteleo.com/ws
    wss://magique2.aristoteleo.com/ws
    wss://magique3.aristoteleo.com/ws

These are load-balanced for reliability and performance.

Best Practices
--------------

1. **Local Development**: Run a local Magique server for development and testing
2. **Production Deployment**: Use SSL/TLS secured servers in production
3. **Load Balancing**: Deploy multiple servers for high-availability systems
4. **Network Proximity**: Deploy servers close to your toolsets for lower latency
5. **Monitoring**: Monitor server health and connection status

Example: Custom Deployment
--------------------------

Here's a complete example of deploying a custom Magique infrastructure::

    # 1. Start Magique servers on different ports
    python -m magique.server --port 8001 &
    python -m magique.server --port 8002 &
    python -m magique.server --port 8003 &
    
    # 2. Configure toolsets to use these servers
    export MAGIQUE_SERVER_URL="ws://localhost:8001/ws|ws://localhost:8002/ws|ws://localhost:8003/ws"
    
    # 3. Start toolsets
    python -m pantheon.toolsets.python --service-name python_tools &
    python -m pantheon.toolsets.web_browse --service-name web_tools &
    
    # 4. Agents will automatically discover services through the servers

Docker Deployment
-----------------

For containerized deployments::

    # docker-compose.yml
    version: '3.8'
    services:
      magique-server:
        image: magique/server
        ports:
          - "8765:8765"
        environment:
          - LOG_LEVEL=INFO
      
      python-toolset:
        image: pantheon/python-toolset
        environment:
          - MAGIQUE_SERVER_URL=ws://magique-server:8765/ws
          - SERVICE_NAME=python_tools
        depends_on:
          - magique-server