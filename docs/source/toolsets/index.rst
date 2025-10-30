Toolsets
========

Toolsets extend agent capabilities by providing access to external functions, APIs, and services. They are provided by the ``pantheon-toolsets`` package and run as independent services that agents can connect to remotely via NATS.

.. toctree::
   :maxdepth: 1

   builtin_toolsets
   rag_system
   custom_toolsets

Installation
------------

Toolsets are provided by the ``pantheon-toolsets`` package::

    pip install pantheon-toolsets

This package includes all built-in toolsets and the framework for creating custom toolsets.

Overview
--------

Pantheon's toolset system is designed around a service-oriented architecture:

- **Service Model**: Each toolset runs as an independent service with its own process
- **Remote Access**: Agents connect to toolsets via WebSocket using service IDs
- **Tool Registration**: Methods decorated with ``@tool`` are automatically exposed
- **Process Isolation**: Tools run in separate processes for security and stability

Architecture
------------

The toolset system consists of three main components:

1. **ToolSet Base Class**: Abstract base class that all toolsets inherit from
2. **Tool Decorator**: Marks methods as tools with execution parameters
3. **Service Infrastructure**: Handles registration, communication, and lifecycle

Key Features
------------

- **Automatic Tool Discovery**: Methods with ``@tool`` decorator are automatically registered
- **Flexible Execution**: Tools can run as local, thread, or process jobs
- **Built-in Security**: Process isolation and sandboxed environments
- **MCP Support**: Toolsets can be exposed as Model Context Protocol servers
- **Lifecycle Management**: Proper setup and cleanup hooks for resource management

