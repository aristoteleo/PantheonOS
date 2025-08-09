# Pantheon CLI

A powerful CLI assistant for Single-Cell/Spatial genomics analysis with multiple tool capabilities.

## Quick Start

### Basic Usage

```bash
# Start with default settings
python -m pantheon.cli

# Start without RAG database
python -m pantheon.cli --disable-rag

# Start with custom model
python -m pantheon.cli --model gpt-4o

# Start with custom workspace
python -m pantheon.cli --workspace /path/to/project
```

### With RAG Database

If you have a RAG database prepared:

```bash
python -m pantheon.cli --rag-db path/to/rag/database
```

Default RAG database location: `tmp/sc_cli_tools_rag/single-cell-cli-tools`

## RAG System Setup

To use the RAG knowledge base, build it from the provided configuration:

```bash
python -m pantheon.toolsets.utils.rag build \
    pantheon/cli/rag_system_config.yaml \
    tmp/pantheon_cli_tools_rag
```

This creates a vector database at `tmp/pantheon_cli_tools_rag/pantheon-cli-tools` with genomics tools documentation.


### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--rag-db` | Path to RAG database | `tmp/pantheon_cli_tools_rag/pantheon-cli-tools` |
| `--model` | AI model to use | `gpt-4.1` |
| `--agent-name` | Name of the agent | `general_bot` |
| `--workspace` | Working directory | Current directory |
| `--instructions` | Custom instructions | Built-in instructions |
| `--disable-rag` | Disable RAG toolset | `False` |
| `--disable-web` | Disable web toolset | `False` |
| `--disable-notebook` | Disable notebook toolset | `False` |
| `--disable-r` | Disable R interpreter toolset | `False` |

## Available Tools

### Core Tools (Always Enabled)
- **Shell**: System commands and genomics tools with auto-installer
- **Python**: Data analysis and visualization (pandas, matplotlib, scanpy)
- **R**: Statistical analysis and Seurat single-cell workflows
- **File Editor**: Read, edit, and create files with diffs
- **Code Search**: Find files (glob), search content (grep), list directories (ls)
- **Todo**: Claude Code-style task management with checkboxes and status tracking

### Optional Tools
- **RAG**: Vector-based knowledge search (requires database)
- **Web**: Web fetch and search using DDGS
- **Notebook**: Jupyter notebook editing (no execution)

## Examples

### Minimal Setup (Core tools only)
```bash
python -m pantheon.cli --disable-rag --disable-web --disable-notebook --disable-r
```

### Python Only Setup
```bash
python -m pantheon.cli --disable-rag --disable-web --disable-notebook --disable-r
```

### R-focused Setup (for Seurat analysis)
```bash
python -m pantheon.cli --disable-web --disable-notebook
```

### Full Featured Setup
```bash
python -m pantheon.cli --rag-db /path/to/rag/db
```

### Custom Instructions
```bash
python -m pantheon.cli --instructions "You are a specialized bioinformatics assistant..."
```




## Features

- 🚀 Fast startup with sensible defaults
- 🔧 Modular toolset architecture  
- 📊 Single-cell genomics specialized
- 🧬 R/Seurat integration with sample data and workflows
- 🐍 Enhanced Python environment with auto-visualization
- 🌐 Web search and fetch capabilities
- 📓 Jupyter notebook support
- 🔍 Advanced code search
- 💾 RAG knowledge base integration
- 🔧 Universal software auto-installer
- ✅ Claude Code-style todo management with status tracking

## Requirements

- Python 3.10+
- Required packages: `fire`, `pantheon-toolsets`, `pantheon-agents`
- Optional: `ddgs` for web search, `nbformat` for notebooks, R for statistical analysis

## Tips

1. If RAG database is not found, the CLI will automatically disable it
2. Use `--disable-*` flags to reduce memory usage if you don't need certain tools
3. The workspace defaults to current directory but can be changed with `--workspace`
4. Custom instructions can completely change the agent's behavior

