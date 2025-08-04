# Home

Pantheon is a distributed LLM-based multi-agent system framework that enables developers to build sophisticated AI applications with multiple collaborating agents. It provides a comprehensive set of tools and abstractions for creating, managing, and orchestrating AI agents in both local and distributed environments.

:::{warning}
**Development Status**: Pantheon is currently in active development. While it's functional and being used in research projects, please use caution when deploying to production environments. APIs may change in future releases.
:::

---

## Quick Access

::::{grid} 2 2 3 3
:gutter: 3

:::{grid-item-card} 📦 Installation
:link: installation
:link-type: doc
:text-align: center

Quick setup guide and requirements for local and distributed deployments.
:::

:::{grid-item-card} 🚀 Quick Start
:link: quickstart  
:link-type: doc
:text-align: center

Build your first agent team with step-by-step tutorials and examples.
:::

:::{grid-item-card} 💡 Core Concepts
:link: concepts
:link-type: doc
:text-align: center

Learn about agents, teams, memory, tools, and distributed architecture.
:::

:::{grid-item-card} 📚 API Reference
:link: api/index
:link-type: doc
:text-align: center

Comprehensive API documentation for all classes and functions.
:::

:::{grid-item-card} 🧑‍💻 Examples & Tutorials
:link: examples/index
:link-type: doc
:text-align: center

Real-world examples and step-by-step tutorials for common use cases.
:::

:::{grid-item-card} 🐙 GitHub Repository
:link: https://github.com/aristoteleo/pantheon-agents
:text-align: center

View source, report issues, and contribute to the project.
:::

::::

---

## Key Features

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} 🤝 Multi-Agent Collaboration
Advanced team coordination with Sequential, Swarm, and MoA (Mixture-of-Agents) patterns for complex workflows.
:::

:::{grid-item-card} 🌐 Distributed Architecture  
Scalable design supporting distributed execution across multiple machines with seamless communication.
:::

:::{grid-item-card} 🛠️ Rich Toolset Ecosystem
Comprehensive built-in tools including Python, R, Shell, Web browsing, RAG, and file operations.
:::

:::{grid-item-card} 💬 ChatRoom Service
Interactive real-time chat interface with Web UI, persistent memory, and multi-threaded conversations.
:::

::::

```{toctree}
:hidden:
:maxdepth: 2

installation
quickstart
concepts
architecture
```

```{toctree}
:caption: Components
:hidden:
:maxdepth: 2

agent/index
team/index
toolsets/index
endpoint_chatroom/index
```

```{toctree}
:caption: Reference
:hidden:
:maxdepth: 2

api/index
guides/index
examples/index
contributing
```