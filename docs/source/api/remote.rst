Remote Module
=============

.. module:: pantheon.remote

The remote module enables distributed agent deployments across multiple machines.

Remote Agent
------------

.. autoclass:: pantheon.remote.agent.RemoteAgent
   :members:
   :undoc-members:
   :show-inheritance:

Overview
--------

The remote module allows agents and tools to run on different machines, enabling:

- Distributed computing
- Resource isolation  
- Scalability
- Specialized hardware usage (GPU, etc.)

Remote Agent Usage
------------------

Basic Remote Agent
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.remote.agent import RemoteAgent

   # Connect to a remote agent
   remote_agent = RemoteAgent(
       name="gpu_processor",
       host="gpu-server.example.com",
       port=8001,
       auth_token="secret_token"
   )

   # Use like a local agent
   result = await remote_agent.run("Process this data with GPU")

Remote Agent in Teams
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SequentialTeam
   from pantheon.agent import Agent
   from pantheon.remote.agent import RemoteAgent

   # Mix local and remote agents
   local_agent = Agent(
       name="coordinator",
       instructions="Coordinate tasks"
   )

   remote_agent = RemoteAgent(
       name="processor",
       host="compute-node-1.local",
       port=8001
   )

   # Create distributed team
   team = SequentialTeam([local_agent, remote_agent])

Remote Toolsets
---------------

Distributed Tools
~~~~~~~~~~~~~~~~~

Tools can also run remotely:

.. code-block:: python

   from pantheon.agent import Agent
   from magique.ai.tools.python import PythonInterpreterToolSet
   from magique.ai.toolset import run_toolsets

   async def setup_remote_tools():
       # Create remote toolset
       toolset = PythonInterpreterToolSet(
           "remote_python",
           host="compute-server.local"
       )
       
       async with run_toolsets([toolset], remote=True):
           agent = Agent(
               name="data_scientist",
               instructions="Analyze data using remote Python"
           )
           
           # Connect to remote toolset
           await agent.remote_toolset(toolset.service_id)
           
           return agent

Architecture
------------

Communication Protocol
~~~~~~~~~~~~~~~~~~~~~~

Remote agents communicate using:

- **Transport**: gRPC or HTTP/WebSocket
- **Serialization**: Protocol Buffers or JSON
- **Security**: TLS encryption and token authentication

Message Flow
~~~~~~~~~~~~

.. code-block:: text

   Client Agent          Network           Remote Agent
        |                  |                    |
        |-- Request ------>|                    |
        |                  |-- Forward -------->|
        |                  |                    |-- Process
        |                  |<-- Response -------|
        |<-- Result -------|                    |

Deployment Patterns
-------------------

Single Remote Agent
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Deploy on remote server
   # remote_server.py
   from pantheon.remote import serve_agent
   from pantheon.agent import Agent

   agent = Agent(
       name="remote_worker",
       instructions="Process tasks remotely"
   )

   serve_agent(agent, host="0.0.0.0", port=8001)

Agent Cluster
~~~~~~~~~~~~~

.. code-block:: python

   # Deploy multiple agents
   agents = []
   
   for i in range(4):
       agent = RemoteAgent(
           name=f"worker_{i}",
           host=f"node-{i}.cluster.local",
           port=8001
       )
       agents.append(agent)

   # Load-balanced team
   team = SwarmCenterTeam([coordinator] + agents)

Configuration
-------------

Remote Agent Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   remote_agent = RemoteAgent(
       name="secure_agent",
       host="agent.example.com",
       port=8443,
       
       # Security
       auth_token="bearer_token",
       use_tls=True,
       cert_path="/path/to/cert.pem",
       
       # Performance
       timeout=30,
       max_retries=3,
       
       # Connection pooling
       pool_size=10,
       keepalive=True
   )

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Remote agent settings
   export PANTHEON_REMOTE_HOST=agent-server.local
   export PANTHEON_REMOTE_PORT=8001
   export PANTHEON_REMOTE_TOKEN=secret_token
   export PANTHEON_REMOTE_TLS=true

Security
--------

Authentication
~~~~~~~~~~~~~~

.. code-block:: python

   # Token-based auth
   remote_agent = RemoteAgent(
       name="secure_agent",
       host="remote.local",
       auth_token=os.getenv("REMOTE_AGENT_TOKEN")
   )

   # Certificate-based auth
   remote_agent = RemoteAgent(
       name="cert_agent",
       host="remote.local",
       client_cert="/path/to/client.crt",
       client_key="/path/to/client.key"
   )

Encryption
~~~~~~~~~~

All remote communications should use TLS:

.. code-block:: python

   remote_agent = RemoteAgent(
       name="encrypted_agent",
       host="remote.local",
       use_tls=True,
       verify_ssl=True
   )

Best Practices
--------------

1. **Network Reliability**: Implement retry logic and timeouts
2. **Security**: Always use authentication and encryption
3. **Monitoring**: Track remote agent health and performance
4. **Resource Management**: Configure appropriate limits
5. **Failover**: Plan for remote agent failures

Troubleshooting
---------------

Connection Issues
~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Debug connection
   import logging
   logging.basicConfig(level=logging.DEBUG)

   remote_agent = RemoteAgent(
       name="debug_agent",
       host="remote.local",
       debug=True
   )

   # Test connectivity
   try:
       await remote_agent.ping()
       print("Connection successful")
   except Exception as e:
       print(f"Connection failed: {e}")

Performance Optimization
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Connection pooling
   remote_agent = RemoteAgent(
       name="optimized_agent",
       host="remote.local",
       pool_size=20,
       keepalive=True,
       keepalive_interval=30
   )

   # Batch requests
   results = await remote_agent.batch_run([
       "Task 1",
       "Task 2", 
       "Task 3"
   ])

Future Enhancements
-------------------

Planned features for the remote module:

- Service discovery
- Automatic load balancing
- Remote agent orchestration
- Metrics and monitoring integration