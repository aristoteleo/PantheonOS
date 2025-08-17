# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pantheon is a framework for building distributed LLM-based multi-agent systems, primarily focused on scientific computing and data analysis. The project emphasizes AI-native architecture with mixed programming language support (Python, R, Julia).

## Common Development Commands

### Installation and Setup
```bash
# Install in development mode
pip install -e .

# Install with development dependencies  
pip install -e ".[dev]"

# Install with specific optional dependencies
pip install -e ".[slack]"
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_agent.py

# Run tests with asyncio support (already configured)
pytest tests/
```

### CLI Usage
```bash
# Start Pantheon CLI with default settings
python -m pantheon.cli

# Start with specific model
python -m pantheon.cli --model claude-sonnet-4-20250514

# Start without RAG database
python -m pantheon.cli --disable-rag

# Start chatroom service
python -m pantheon.chatroom
```

### Documentation
```bash
# Build documentation (from docs/ directory)
cd docs && make html

# Live documentation development
cd docs && make dev

# Install documentation dependencies
cd docs && make install
```

### RAG Database Setup
```bash
# Build RAG database for CLI tools
python -m pantheon.toolsets.utils.rag build \
    pantheon/cli/rag_system_config.yaml \
    tmp/pantheon_cli_tools_rag
```

## Architecture Overview

### Core Components

- **Agent System (`pantheon/agent.py`)**: Core LLM agent implementation with OpenAI/LiteLLM integration, tool calling, and memory management
- **Team System (`pantheon/team.py`)**: Multi-agent coordination with SwarmTeam, SequentialTeam, and MoATeam patterns
- **Memory (`pantheon/memory.py`)**: Persistent conversation memory system
- **Remote Agents (`pantheon/remote/`)**: Distributed agent execution using Magique infrastructure
- **Smart Functions (`pantheon/smart_func.py`)**: AI-powered function generation and execution

### Key Modules

- **CLI (`pantheon/cli/`)**: Main command-line interface with model management, API key handling, and scientific toolsets
- **REPL (`pantheon/repl/`)**: Interactive environment for agents and teams
- **Chatroom (`pantheon/chatroom/`)**: Web-based multi-agent collaboration interface
- **Generator (`pantheon/generator/`)**: AI-powered toolset generation system
- **Slack Integration (`pantheon/slack/`)**: Slack app for team collaboration

### Toolset Architecture

The system uses built-in `pantheon.toolset` modules for:
- Python/R/Julia interpreters
- File operations and code search
- Web browsing and RAG systems
- Bioinformatics analysis pipelines
- External toolset loading

### Agent Types and Patterns

1. **Single Agents**: Basic LLM agents with tool calling
2. **SwarmTeam**: Handoff and routine patterns (OpenAI Swarm-like)
3. **SequentialTeam**: Sequential agent execution
4. **MoATeam**: Mixture-of-Agents for improved responses

### Memory and Context Management

- Persistent memory using file-based storage
- Context variables for agent state
- Message history management with OpenAI format compatibility

## Key Configuration Files

- `pyproject.toml`: Project dependencies and metadata
- `pantheon/cli/rag_system_config.yaml`: RAG database configuration
- `pantheon/chatroom/default_agents_templates.yaml`: Default agent templates
- `configs/bio_knowledge.yaml`: Bioinformatics knowledge configuration

## Important Patterns

### Agent Creation
```python
from pantheon.agent import Agent

agent = Agent(
    name="my_agent",
    model="gpt-4.1-mini",
    instructions="Custom instructions",
    toolsets=[...]  # Toolset instances
)
```

### Team Usage
```python
from pantheon.team import SwarmTeam

team = SwarmTeam(agents=[agent1, agent2])
await team.run(message)
```

### Remote Agent Deployment
```python
from pantheon.remote.agent import RemoteAgent

remote_agent = RemoteAgent.from_agent(
    agent, 
    service_name="my_service"
)
```

## Testing Approach

- Uses `pytest` with asyncio support
- Test files in `tests/` directory
- Covers agent functionality, teams, toolsets, and remote execution
- Mock data in `tests/data/`

## Dependencies

Core dependencies include:
- `litellm`: Multi-provider LLM client
- `magique`: Distributed system infrastructure  
- `pantheon.toolset`: Built-in scientific computing toolsets
- `executor-engine`: Code execution framework
- `funcdesc`: Function description parsing

Development dependencies:
- `pytest` + `pytest-asyncio`: Testing framework
- `ipdb` + `ipython`: Debugging tools
- `textual-dev`: TUI development