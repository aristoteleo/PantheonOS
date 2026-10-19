Distributed Deployment
======================

Running Pantheon across multiple machines.

Overview
--------

Distributed deployment enables:

- Scaling across multiple machines
- Remote tool execution
- Centralized agent coordination
- High availability setups

.. code-block:: text

   ┌─────────────────────────────────────────────────┐
   │                 NATS Server                      │
   │         (Message Broker / Coordinator)           │
   └─────────────────────┬───────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
   ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
   │  Client   │   │  Worker   │   │  Worker   │
   │  (REPL)   │   │ (Tools)   │   │ (Tools)   │
   └───────────┘   └───────────┘   └───────────┘

Architecture
------------

**Components:**

- **NATS Server**: Central message broker
- **Clients**: REPL, UI, or API interfaces
- **Workers**: Tool execution endpoints

Configuration
-------------

**NATS Setup**

Install and run NATS:

.. code-block:: bash

   # Install NATS
   # macOS
   brew install nats-server

   # Linux
   wget https://github.com/nats-io/nats-server/releases/download/v2.10.0/nats-server-v2.10.0-linux-amd64.tar.gz
   tar -xzf nats-server-*.tar.gz

   # Run NATS
   nats-server

**Settings Configuration**

In ``settings.json``:

.. code-block:: json

   {
     "remote": {
       "backend": "nats",
       "nats_url": "nats://localhost:4222"
     },
     "endpoint": {
       "execution_mode": "remote"
     }
   }

Starting Workers
----------------

**Tool Worker**

.. code-block:: bash

   python -m pantheon.endpoint --mode worker

**With Specific Tools**

.. code-block:: bash

   python -m pantheon.endpoint --mode worker --tools file_manager,shell

**Worker Configuration**

.. code-block:: json

   {
     "worker": {
       "name": "worker-1",
       "tools": ["file_manager", "shell", "python_interpreter"],
       "max_concurrent": 10,
       "timeout": 120
     }
   }

Client Connection
-----------------

**REPL**

.. code-block:: bash

   pantheon cli --remote nats://localhost:4222

**Python API**

.. code-block:: python

   from pantheon.agent import Agent
   from pantheonos.remote import RemoteEndpoint

   endpoint = RemoteEndpoint(
       backend="nats",
       nats_url="nats://localhost:4222"
   )

   agent = Agent(
       name="assistant",
       endpoint=endpoint
   )

   response = await agent.run("Execute this on remote worker")

Multi-Worker Setup
------------------

Scale with multiple workers:

.. code-block:: text

   ┌─────────────────────────────────────────┐
   │            NATS Cluster                  │
   │  ┌──────┐  ┌──────┐  ┌──────┐          │
   │  │ nats │──│ nats │──│ nats │          │
   │  └──────┘  └──────┘  └──────┘          │
   └────────────────┬────────────────────────┘
                    │
   ┌────────────────┼────────────────┐
   │                │                │
   ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
   │Worker 1 │ │Worker 2 │ │Worker 3 │
   │(Files)  │ │(Python) │ │(Shell)  │
   └─────────┘ └─────────┘ └─────────┘

**Load Balancing**

NATS automatically distributes requests across workers.

**Specialized Workers**

.. code-block:: bash

   # Worker 1: File operations
   python -m pantheon.endpoint --mode worker --tools file_manager

   # Worker 2: Python execution
   python -m pantheon.endpoint --mode worker --tools python_interpreter

   # Worker 3: Shell commands
   python -m pantheon.endpoint --mode worker --tools shell

High Availability
-----------------

**NATS Cluster**

.. code-block:: bash

   # Node 1
   nats-server --cluster nats://0.0.0.0:6222 --routes nats://node2:6222,nats://node3:6222

   # Node 2
   nats-server --cluster nats://0.0.0.0:6222 --routes nats://node1:6222,nats://node3:6222

   # Node 3
   nats-server --cluster nats://0.0.0.0:6222 --routes nats://node1:6222,nats://node2:6222

**Client Failover**

.. code-block:: json

   {
     "remote": {
       "nats_url": "nats://node1:4222,nats://node2:4222,nats://node3:4222"
     }
   }

Security
--------

**Authentication**

.. code-block:: json

   {
     "remote": {
       "nats_url": "nats://user:password@localhost:4222"
     }
   }

**TLS**

.. code-block:: json

   {
     "remote": {
       "nats_url": "tls://localhost:4222",
       "tls_cert": "/path/to/cert.pem",
       "tls_key": "/path/to/key.pem"
     }
   }

**Token Authentication**

.. code-block:: json

   {
     "remote": {
       "nats_url": "nats://localhost:4222",
       "nats_token": "your-secret-token"
     }
   }

Monitoring
----------

**NATS Monitoring**

.. code-block:: bash

   # Enable monitoring
   nats-server --http_port 8222

   # View stats
   curl http://localhost:8222/varz

**Worker Health**

.. code-block:: python

   from pantheonos.remote import RemoteEndpoint

   endpoint = RemoteEndpoint(...)

   # Check worker status
   status = await endpoint.health_check()
   print(f"Workers online: {status['workers']}")

Docker Deployment
-----------------

**docker-compose.yml**

.. code-block:: yaml

   version: '3'
   services:
     nats:
       image: nats:latest
       ports:
         - "4222:4222"
         - "8222:8222"

     worker:
       build: .
       command: python -m pantheon.endpoint --mode worker
       environment:
         - NATS_URL=nats://nats:4222
       depends_on:
         - nats
       deploy:
         replicas: 3

     repl:
       build: .
       command: pantheon cli --remote nats://nats:4222
       depends_on:
         - nats
         - worker
       stdin_open: true
       tty: true

Kubernetes Deployment
---------------------

**Basic Deployment**

.. code-block:: yaml

   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: pantheon-worker
   spec:
     replicas: 3
     selector:
       matchLabels:
         app: pantheon-worker
     template:
       metadata:
         labels:
           app: pantheon-worker
       spec:
         containers:
         - name: worker
           image: pantheon:latest
           command: ["python", "-m", "pantheon.endpoint", "--mode", "worker"]
           env:
           - name: NATS_URL
             value: "nats://nats-service:4222"

Best Practices
--------------

1. **Start Simple**: Begin with single NATS server, scale as needed
2. **Monitor Workers**: Track worker health and performance
3. **Secure Communications**: Use TLS in production
4. **Plan Capacity**: Size workers based on expected load
5. **Handle Failures**: Implement retry logic and failover
