Remote Agent
============

.. warning::
   
   The Remote Agent API is currently unstable and subject to change. Use with caution in production environments.

Remote Agents enable distributed deployment of agents across multiple machines, providing scalability, resource isolation, and flexible architectures for complex systems.
Remote Agents allow you to:

- Deploy agents on separate servers
- Scale agent capacity horizontally
- Isolate resource-intensive agents
- Create distributed multi-agent systems
- Share agents across applications

Starting a Remote Agent
-----------------------

Server Side
~~~~~~~~~~~

Deploy an agent as a network service:

.. code-block:: python

   from pantheon.agent import Agent
   import asyncio
   
   async def start_remote_agent():
       # Create agent
       agent = Agent(
           name="expert_agent",
           instructions="You are a domain expert in data science.",
           model="gpt-4o"
       )
       
       # Add tools
       @agent.tool
       def analyze_data(data: list) -> dict:
           """Analyze numerical data."""
           return {
               "mean": sum(data) / len(data),
               "max": max(data),
               "min": min(data)
           }
       
       # Start as service
       await agent.start_service(
           host="0.0.0.0",
           port=8000,
           auth_token="your-secret-token"
       )
       
       print(f"Agent '{agent.name}' running on port 8000")
       
       # Keep running
       await asyncio.Event().wait()
   
   if __name__ == "__main__":
       asyncio.run(start_remote_agent())

Client Side
~~~~~~~~~~~

Connect to a remote agent:

.. code-block:: python

   from pantheon.remote import RemoteAgent
   import asyncio
   
   async def use_remote_agent():
       # Connect to remote agent
       agent = RemoteAgent(
           url="http://agent-server:8000",
           auth_token="your-secret-token"
       )
       
       # Use like a local agent
       response = await agent.run([
           {"role": "user", "content": "Analyze this data: [1, 2, 3, 4, 5]"}
       ])
       
       print(response.messages[-1]["content"])
   
   if __name__ == "__main__":
       asyncio.run(use_remote_agent())

Configuration Options
---------------------

Service Configuration
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   await agent.start_service(
       host="0.0.0.0",              # Listen address
       port=8000,                   # Port number
       auth_token="secret",         # Authentication token
       ssl_cert="cert.pem",         # SSL certificate (optional)
       ssl_key="key.pem",           # SSL key (optional)
       max_connections=100,         # Connection limit
       timeout=300,                 # Request timeout (seconds)
       cors_origins=["*"],          # CORS settings
       log_level="INFO"             # Logging level
   )

Client Configuration
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   agent = RemoteAgent(
       url="https://agent-server:8000",
       auth_token="secret",
       timeout=60,                  # Request timeout
       retry_attempts=3,            # Retry failed requests
       retry_delay=1.0,             # Delay between retries
       verify_ssl=True,             # SSL verification
       connection_pool_size=10      # Connection pool size
   )

Authentication
--------------

Token Authentication
~~~~~~~~~~~~~~~~~~~~

Basic token-based authentication:

.. code-block:: python

   # Server side
   await agent.start_service(
       auth_token="your-secret-token"
   )
   
   # Client side
   agent = RemoteAgent(
       url="http://localhost:8000",
       auth_token="your-secret-token"
   )

Custom Authentication
~~~~~~~~~~~~~~~~~~~~~

Implement custom authentication:

.. code-block:: python

   from pantheon.remote import AuthHandler
   
   class APIKeyAuth(AuthHandler):
       def __init__(self, api_keys):
           self.api_keys = api_keys
       
       async def authenticate(self, request):
           api_key = request.headers.get("X-API-Key")
           return api_key in self.api_keys
       
       async def get_user(self, request):
           api_key = request.headers.get("X-API-Key")
           return {"api_key": api_key, "tier": self.get_tier(api_key)}
   
   # Use custom auth
   auth = APIKeyAuth(["key1", "key2", "key3"])
   await agent.start_service(auth_handler=auth)

Advanced Features
-----------------

Health Checks
~~~~~~~~~~~~~

Monitor remote agent health:

.. code-block:: python

   # Client side
   agent = RemoteAgent("http://agent-server:8000")
   
   # Check health
   health = await agent.health_check()
   print(f"Status: {health['status']}")
   print(f"Uptime: {health['uptime']} seconds")
   print(f"Active requests: {health['active_requests']}")
   
   # Server side - custom health check
   async def custom_health_check():
       return {
           "status": "healthy",
           "model": agent.model,
           "tools": len(agent.tools),
           "memory_usage": get_memory_usage(),
           "custom_metric": calculate_metric()
       }
   
   await agent.start_service(
       health_check_handler=custom_health_check
   )

Streaming Support
~~~~~~~~~~~~~~~~~

Stream responses from remote agents:

.. code-block:: python

   # Stream responses
   async for chunk in agent.run_stream([
       {"role": "user", "content": "Tell me a long story"}
   ]):
       print(chunk.get("content", ""), end="", flush=True)

Load Balancing
~~~~~~~~~~~~~~

Distribute requests across multiple agents:

.. code-block:: python

   from pantheon.remote import RemoteAgentPool
   
   # Create pool of remote agents
   pool = RemoteAgentPool([
       "http://agent1:8000",
       "http://agent2:8000",
       "http://agent3:8000"
   ], auth_token="secret")
   
   # Automatic load balancing
   response = await pool.run(messages)
   
   # With custom strategy
   pool = RemoteAgentPool(
       endpoints,
       strategy="least_connections",  # or "round_robin", "random"
       health_check_interval=30
   )

Deployment Patterns
-------------------

Docker Deployment
~~~~~~~~~~~~~~~~~

Dockerfile for remote agent:

.. code-block:: dockerfile

   FROM python:3.9-slim
   
   WORKDIR /app
   
   COPY requirements.txt .
   RUN pip install -r requirements.txt
   
   COPY agent.py .
   
   EXPOSE 8000
   
   CMD ["python", "agent.py"]

Docker Compose setup:

.. code-block:: yaml

   version: '3.8'
   
   services:
     expert-agent:
       build: ./agents/expert
       ports:
         - "8001:8000"
       environment:
         - OPENAI_API_KEY=${OPENAI_API_KEY}
         - AUTH_TOKEN=${AUTH_TOKEN}
       restart: unless-stopped
       
     research-agent:
       build: ./agents/research
       ports:
         - "8002:8000"
       environment:
         - OPENAI_API_KEY=${OPENAI_API_KEY}
         - AUTH_TOKEN=${AUTH_TOKEN}
       restart: unless-stopped

Kubernetes Deployment
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: remote-agent
   spec:
     replicas: 3
     selector:
       matchLabels:
         app: remote-agent
     template:
       metadata:
         labels:
           app: remote-agent
       spec:
         containers:
         - name: agent
           image: myregistry/remote-agent:latest
           ports:
           - containerPort: 8000
           env:
           - name: OPENAI_API_KEY
             valueFrom:
               secretKeyRef:
                 name: api-keys
                 key: openai
           - name: AUTH_TOKEN
             valueFrom:
               secretKeyRef:
                 name: agent-auth
                 key: token
           livenessProbe:
             httpGet:
               path: /health
               port: 8000
             initialDelaySeconds: 30
             periodSeconds: 10

Multi-Agent Systems
-------------------

Distributed Team
~~~~~~~~~~~~~~~~

Create teams with remote agents:

.. code-block:: python

   from pantheon.team import SequentialTeam
   from pantheon.remote import RemoteAgent
   
   # Remote agents on different servers
   researcher = RemoteAgent("http://research-server:8000", auth_token="token1")
   analyst = RemoteAgent("http://analytics-server:8000", auth_token="token2")
   writer = RemoteAgent("http://writing-server:8000", auth_token="token3")
   
   # Create distributed team
   team = SequentialTeam([researcher, analyst, writer])
   
   # Use team normally
   result = await team.run("Analyze market trends in AI")

Agent Registry
~~~~~~~~~~~~~~

Central registry for remote agents:

.. code-block:: python

   from pantheon.remote import AgentRegistry
   
   # Create registry
   registry = AgentRegistry("redis://localhost:6379")
   
   # Register agents
   await registry.register(
       name="expert-1",
       url="http://expert1:8000",
       capabilities=["data_science", "machine_learning"],
       metadata={"model": "gpt-4", "region": "us-west"}
   )
   
   # Discover agents
   ml_agents = await registry.find(capability="machine_learning")
   us_agents = await registry.find(metadata={"region": "us-west"})
   
   # Get specific agent
   agent_info = await registry.get("expert-1")
   agent = RemoteAgent(agent_info["url"])

Monitoring and Logging
----------------------

Metrics Collection
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.remote import MetricsCollector
   
   # Server side
   metrics = MetricsCollector()
   
   await agent.start_service(
       metrics_collector=metrics,
       metrics_endpoint="/metrics"  # Prometheus compatible
   )
   
   # Client side monitoring
   agent = RemoteAgent(url, enable_metrics=True)
   
   # Get metrics
   metrics = await agent.get_metrics()
   print(f"Total requests: {metrics['total_requests']}")
   print(f"Average latency: {metrics['avg_latency_ms']}ms")

Distributed Tracing
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.remote import TracingMiddleware
   import opentelemetry
   
   # Enable tracing
   tracing = TracingMiddleware(
       service_name="remote-agent",
       jaeger_endpoint="http://jaeger:14268/api/traces"
   )
   
   await agent.start_service(
       middleware=[tracing]
   )

Security Best Practices
-----------------------

1. **Use HTTPS**: Always use SSL/TLS in production
2. **Strong Authentication**: Use secure tokens or certificates
3. **Network Security**: Restrict network access with firewalls
4. **Input Validation**: Validate all incoming requests
5. **Rate Limiting**: Implement rate limiting to prevent abuse
6. **Monitoring**: Log and monitor all access attempts

Example: Secure Setup
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.remote import RateLimiter, IPWhitelist
   
   # Comprehensive security setup
   await agent.start_service(
       host="0.0.0.0",
       port=8443,
       ssl_cert="cert.pem",
       ssl_key="key.pem",
       auth_token=os.environ["SECURE_TOKEN"],
       middleware=[
           IPWhitelist(["10.0.0.0/8", "192.168.0.0/16"]),
           RateLimiter(requests_per_minute=60),
       ],
       cors_origins=["https://trusted-app.com"],
       log_level="INFO"
   )

Performance Optimization
------------------------

- Use connection pooling for better resource utilization
- Implement caching for frequently requested data
- Enable compression for large responses
- Use async operations throughout
- Monitor and optimize based on metrics