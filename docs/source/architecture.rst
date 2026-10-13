System Architecture
===================

Pantheon is a distributed multi-agent AI system that enables developers to build sophisticated AI applications with multiple collaborating agents. The system consists of three main components that work together seamlessly.

Overview
--------

The Pantheon ecosystem is composed of three primary repositories:

- **PantheonOS**: Core agent framework and orchestration
- **pantheon-toolsets**: Distributed toolset system for agent capabilities  
- **pantheon-interfaces**: User interfaces for interacting with the system

Component Architecture
----------------------

PantheonOS (pantheon-agents)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Repository: https://github.com/aristoteleo/PantheonOS

The core framework that provides the foundation for the entire system:

**Key Components:**

- **Agent**: The fundamental building block - AI-powered entities with instructions, memory, and tool capabilities
- **Memory**: Persistent storage system for maintaining context across interactions
- **Team**: Multi-agent collaboration via ``PantheonTeam`` (team mode: agents delegate with ``call_agent``; agent mode: one agent starts sub-agents)
- **PantheonChatroom**: A service layer built on top of Team components that:
  
  - Hosts a server to handle frontend requests
  - Orchestrates multiple agents to complete tasks
  - Interfaces with Pantheon-UI and Pantheon-Endpoint
  - Manages session state and conversation history

**Architecture:**

.. mermaid::

   graph TD
       Chatroom[PantheonChatroom<br/>Service]
       Team[Team<br/>Orchestrator]
       Agents[Agents<br/>with Memory]
       
       Chatroom --> Team
       Team --> Agents

pantheon-toolsets
~~~~~~~~~~~~~~~~~

Repository: https://github.com/aristoteleo/pantheon-toolsets

A distributed toolset system that provides various capabilities to PantheonOS agents:

**Built-in Toolsets:**

- **PythonInterpreter**: Execute Python code in sandboxed environments
- **RInterpreter**: R language execution for statistical computing
- **WebBrowse**: Web scraping and browsing capabilities
- **VectorRAG**: Vector-based retrieval augmented generation
- **FileManager**: File system operations
- **Shell**: System command execution

**Endpoint Component:**

The endpoint is a crucial component that:

- Hosts a collection of toolsets as a service
- Provides backend computational support for PantheonChatroom
- Enables distributed deployment of resource-intensive tools
- Manages tool lifecycle and resource allocation

**Example Endpoint Configuration:**

.. code-block:: yaml

    # endpoint.yaml
    name: "compute-endpoint"
    toolsets:
      - type: python_interpreter
        name: "python_compute"
      - type: r_interpreter
        name: "r_stats"
      - type: vector_rag
        name: "knowledge_base"

pantheon-interfaces
~~~~~~~~~~~~~~~~~~~

User-facing interfaces for interacting with the Pantheon system:

**pantheon-ui**

Repository: https://github.com/aristoteleo/pantheon-ui

- Web-based frontend interface built with modern frameworks
- Direct integration with PantheonChatroom
- Connects users with chatrooms and endpoints
- Real-time streaming of agent responses
- Session management and history

**pantheon-cli** (in pantheon-agents repo)

- Command-line interface for developers
- Direct agent interaction
- Debugging and testing capabilities

**pantheon-slack** (in pantheon-agents repo)

- Slack bot integration
- Team collaboration features
- Notification system

Communication Architecture
--------------------------

All components communicate through **Magique**, a WebSocket-based communication library:

**Magique Architecture:**

.. mermaid::

   graph LR
       Client[Client] <-->|WebSocket| Server[Server]
       Server <-->|WebSocket| Worker[Worker]
       Client -.->|Request| Worker
       Worker -.->|Response| Client

- **Server**: Public-facing server accessible over the internet
- **Client**: Initiates requests and receives responses
- **Worker**: Processes requests and returns results

**Communication Flows:**

1. **Agent ↔ Toolset**: Agents request tool execution from local or remote toolsets
2. **Agent ↔ Agent**: Inter-agent communication for team collaboration
3. **Endpoint ↔ Chatroom**: Endpoints provide computational resources to chatrooms
4. **Chatroom ↔ UI**: Real-time bidirectional communication with users

System Integration
------------------

.. mermaid::

   graph TB
       UI[Pantheon-UI] -->|WebSocket| Chatroom[PantheonChatroom]
       CLI[Pantheon-CLI] -->|Direct| Chatroom
       Slack[Pantheon-Slack] -->|API| Chatroom
       
       Chatroom -->|Orchestrates| Team[Agent Team]
       Team -->|Coordinates| Agent1[Agent 1]
       Team -->|Coordinates| Agent2[Agent 2]
       Team -->|Coordinates| AgentN[Agent N]
       
       Agent1 -->|Magique| Endpoint1[Endpoint 1]
       Agent2 -->|Magique| Endpoint2[Endpoint 2]
       
       Endpoint1 -->|Hosts| Tools1[Toolset Collection]
       Endpoint2 -->|Hosts| Tools2[Toolset Collection]
       
       Agent1 -.->|Memory| Storage[(Memory Storage)]
       Agent2 -.->|Memory| Storage
       
       Chatroom -.->|Sessions| DB[(Database)]

Deployment Scenarios
--------------------

**Local Development:**

.. mermaid::

   graph TD
       subgraph "Single Machine"
           Chatroom[PantheonChatroom<br/>localhost:8000]
           Endpoint[Endpoint<br/>localhost:8001]
           UI[UI<br/>localhost:3000]
       end

**Distributed Production:**

.. mermaid::

   graph TD
       UI[UI Servers<br/>CDN + LB]
       Chatroom[Chatroom Cluster<br/>Kubernetes]
       Endpoint1[Endpoint 1<br/>GPU Server]
       Endpoint2[Endpoint 2<br/>CPU Cluster]
       
       UI --> Chatroom
       Chatroom --> Endpoint1
       Chatroom --> Endpoint2

Key Design Principles
---------------------

1. **Modularity**: Each component can be developed and deployed independently
2. **Scalability**: Horizontal scaling through distributed endpoints
3. **Flexibility**: Mix and match different agents, tools, and interfaces
4. **Fault Tolerance**: Components can fail without bringing down the system
5. **Extensibility**: Easy to add new agents, tools, and interfaces

Example Usage Flow
------------------

1. User sends a message through Pantheon-UI
2. UI forwards the request to PantheonChatroom via WebSocket
3. Chatroom activates the appropriate Team configuration
4. Team orchestrates multiple Agents to handle the request
5. Agents request tools from local or remote Endpoints via Magique
6. Endpoints execute tools and return results
7. Agents collaborate to formulate a response
8. Chatroom streams the response back to UI
9. UI displays the result to the user

This architecture enables building complex AI systems that can scale from simple single-agent applications to sophisticated multi-agent systems with distributed computing capabilities.