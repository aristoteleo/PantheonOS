# External Toolset Module System Design

## Overview
Design a simple and easy-to-use external toolset module system that allows users to add custom toolsets in the `./ext_toolsets` directory without modifying Pantheon core code.

## 1. External Module Directory Structure

```
./ext_toolsets/                    # External toolsets root directory
├── __init__.py                    # Empty file, marks as Python package
├── ext_loader.py                  # External module loader
├── base.py                        # Simplified base class (independent from pantheon)
├── README.md                      # Usage instructions
│
├── example_tool/                  # Example toolset
│   ├── __init__.py               # Toolset exports
│   ├── toolset.py                # Main implementation
│   └── config.json               # Toolset metadata
│
├── web_scraper/                   # Web scraper toolset
│   ├── __init__.py
│   ├── toolset.py
│   └── config.json
│
└── data_processor/                # Data processor toolset
    ├── __init__.py
    ├── toolset.py
    └── config.json
```

## 2. Core Components

### 2.1 ExternalToolSet Base Class

A simplified base class (`ext_toolsets/base.py`) that provides:

```python
"""Simplified base class for external toolsets"""

import json
import inspect
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
from functools import wraps
from rich.console import Console

class ExternalToolSet:
    """Base class for external toolsets"""
    
    def __init__(self, name: str = None, **kwargs):
        self.name = name or self.__class__.__name__.lower().replace('toolset', '')
        self.console = Console()
        self.workspace_path = Path(kwargs.get('workspace_path', './workspace'))
        self.workspace_path.mkdir(exist_ok=True)
        
        # Load configuration
        self.config = self._load_config()
        self.console.print(f"[green]Initialized {self.name} toolset[/green]")
    
    def _load_config(self) -> Dict[str, Any]:
        """Load toolset configuration from config.json"""
        config_path = Path(__file__).parent / "config.json"
        if config_path.exists():
            with open(config_path, 'r') as f:
                return json.load(f)
        return {"name": self.name, "version": "1.0.0", "description": "External toolset"}
```

### 2.2 Tool Decorator

Enhanced `@tool` decorator compatible with Pantheon:

```python
def tool(func: Callable = None, **kwargs):
    """Enhanced tool decorator compatible with Pantheon"""
    def decorator(f):
        # Mark function as a tool
        f._is_tool = True
        f._tool_kwargs = kwargs
        
        # Generate OpenAI-compatible schema from function signature
        sig = inspect.signature(f)
        schema = {
            "name": f.__name__,
            "description": (f.__doc__ or f.__name__).strip(),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
        
        for param_name, param in sig.parameters.items():
            if param_name in ['self']:
                continue
                
            param_schema = {"type": "string"}  # Default to string for OpenAI compatibility
            
            # Handle type annotations
            if param.annotation != inspect.Parameter.empty:
                if param.annotation == int:
                    param_schema["type"] = "integer"
                elif param.annotation == float:
                    param_schema["type"] = "number"
                elif param.annotation == bool:
                    param_schema["type"] = "boolean"
            
            schema["parameters"]["properties"][param_name] = param_schema
            
            if param.default == inspect.Parameter.empty:
                schema["parameters"]["required"].append(param_name)
        
        f._openai_schema = schema
        return f
    
    return decorator(func) if func else decorator
```

### 2.3 Dynamic Loading System

External module loader (`ext_toolsets/ext_loader.py`):

```python
"""External toolsets loader for Pantheon integration"""

import sys
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, List

class ExternalToolsetsLoader:
    """Loader for external toolsets"""
    
    def __init__(self, ext_dir: Path):
        self.ext_dir = Path(ext_dir)
        self.loaded_toolsets = {}
    
    def discover_toolsets(self, filter_list: Optional[List[str]] = None) -> Dict[str, Any]:
        """Discover available toolsets in ext_dir"""
        toolsets = {}
        
        if not self.ext_dir.exists():
            return toolsets
        
        for item in self.ext_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Check if should be filtered
                if filter_list and item.name not in filter_list:
                    continue
                
                # Check for required files
                if (item / '__init__.py').exists() and (item / 'toolset.py').exists():
                    config_path = item / 'config.json'
                    config = {}
                    if config_path.exists():
                        import json
                        with open(config_path, 'r') as f:
                            config = json.load(f)
                    
                    toolsets[item.name] = {
                        'path': item,
                        'config': config,
                        'module_name': f"ext_toolsets.{item.name}"
                    }
        
        return toolsets
    
    def load_toolset(self, name: str) -> Optional[Any]:
        """Load a specific toolset"""
        toolsets = self.discover_toolsets([name])
        if name not in toolsets:
            return None
        
        try:
            # Import the module
            module_name = toolsets[name]['module_name']
            module = importlib.import_module(module_name)
            
            # Find the toolset class
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    hasattr(attr, '__bases__') and
                    any('ExternalToolSet' in str(base) for base in attr.__bases__)):
                    return attr
            
        except Exception as e:
            print(f"Error loading toolset {name}: {e}")
        
        return None
    
    def register_with_agent(self, agent, filter_list: Optional[List[str]] = None) -> str:
        """Register toolsets with Pantheon agent"""
        toolsets = self.discover_toolsets(filter_list)
        instructions = ""
        
        for name, info in toolsets.items():
            try:
                toolset_class = self.load_toolset(name)
                if toolset_class:
                    # Initialize toolset
                    toolset_instance = toolset_class()
                    
                    # Register with agent
                    agent.toolset(toolset_instance)
                    
                    # Generate instructions
                    config = info.get('config', {})
                    description = config.get('description', f'{name} external toolset')
                    capabilities = config.get('capabilities', [])
                    
                    instructions += f"\n\n{name.upper()} TOOLSET:\n"
                    instructions += f"- Description: {description}\n"
                    if capabilities:
                        instructions += f"- Capabilities: {', '.join(capabilities)}\n"
                    
                    # Add tool descriptions
                    tools = []
                    for attr_name in dir(toolset_instance):
                        attr = getattr(toolset_instance, attr_name)
                        if hasattr(attr, '_is_tool'):
                            doc = attr.__doc__ or attr_name
                            tools.append(f"  - {attr_name}: {doc.split('\n')[0].strip()}")
                    
                    if tools:
                        instructions += f"- Available tools:\n" + '\n'.join(tools) + "\n"
                    
                    print(f"✅ Loaded external toolset: {name}")
                
            except Exception as e:
                print(f"⚠️ Failed to load toolset {name}: {e}")
        
        return instructions

# Global loader instance
ext_loader = ExternalToolsetsLoader(Path("./ext_toolsets"))
```

## 3. Integration with Pantheon CLI

### 3.1 CLI Integration Points

In `pantheon/cli/core.py`, external toolsets are loaded via:

```python
def load_external_toolsets(ext_dir: str = "./ext_toolsets") -> Optional[Any]:
    """Load external toolset loader if available"""
    ext_path = Path(ext_dir).resolve()
    
    if not ext_path.exists():
        return None
    
    # Add to Python path
    if str(ext_path) not in sys.path:
        sys.path.insert(0, str(ext_path))
    
    try:
        from ext_loader import ext_loader
        return ext_loader
    except ImportError:
        return None
```

### 3.2 Agent Registration

```python
# Register external toolsets if available
if ext_loader:
    try:
        ext_instructions = ext_loader.register_with_agent(
            agent, 
            toolset_list if ext_toolsets else None
        )
        
        # Update agent instructions if external toolsets were loaded
        if ext_instructions and not instructions:
            # Append external instructions to default
            agent.instructions = DEFAULT_INSTRUCTIONS + ext_instructions
            print(f"📖 Updated agent with external toolset instructions")
    except Exception as e:
        print(f"[Warning] Failed to load external toolsets: {e}")
```

## 4. Configuration System

### 4.1 Toolset Configuration (config.json)

```json
{
    "name": "web_scraper",
    "version": "1.0.0",
    "description": "Advanced web scraping toolset with rate limiting and caching",
    "author": "Pantheon User",
    "dependencies": ["requests", "beautifulsoup4", "lxml"],
    "tags": ["web", "scraping", "http", "parsing"],
    "capabilities": ["fetch_page", "extract_text", "extract_links", "cache_management"],
    "settings": {
        "default_timeout": 30,
        "rate_limit": 1.0,
        "cache_enabled": true
    }
}
```

### 4.2 Global Configuration

Support for global configuration in `ext_toolsets/config.json`:

```json
{
    "version": "1.0.0",
    "default_settings": {
        "workspace_path": "./workspace",
        "log_level": "INFO",
        "console_width": 120
    },
    "toolset_filters": {
        "enabled": ["web_scraper", "data_processor"],
        "disabled": ["experimental_tool"]
    }
}
```

## 5. Development Workflow

### 5.1 Creating New Toolsets

1. **Create directory structure**:
   ```bash
   mkdir ext_toolsets/my_toolset
   cd ext_toolsets/my_toolset
   ```

2. **Create basic files**:
   - `__init__.py` - Export toolset class
   - `toolset.py` - Main implementation
   - `config.json` - Metadata and settings

3. **Implement toolset**:
   ```python
   from base import ExternalToolSet, tool
   
   class MyToolSet(ExternalToolSet):
       @tool
       def my_function(self, param: str) -> dict:
           """My function description"""
           return {"status": "success", "data": param}
   ```

4. **Test integration**:
   ```bash
   python -m pantheon.cli --ext-toolsets my_toolset
   ```

### 5.2 Quality Standards

- **Error Handling**: All tools should return consistent format
- **Documentation**: Complete docstrings for all public methods
- **Type Safety**: Use appropriate type hints
- **OpenAI Compatibility**: Use simple parameter types (str, int, float, bool)
- **Rich Output**: Use console formatting for user feedback
- **Configuration**: Support for runtime configuration via config.json

## 6. Advanced Features

### 6.1 Dependency Management

Automatic dependency checking:

```python
def _check_dependencies(self):
    """Check if required dependencies are available"""
    dependencies = self.config.get("dependencies", [])
    for dep in dependencies:
        try:
            __import__(dep)
        except ImportError:
            self.console.print(f"[yellow]Warning: {dep} not available. Install with: pip install {dep}[/yellow]")
```

### 6.2 Workspace Management

Isolated workspace for each toolset:

```python
def _setup_workspace(self):
    """Setup toolset-specific workspace"""
    self.workspace_path = Path(f"./workspace/{self.name}")
    self.workspace_path.mkdir(parents=True, exist_ok=True)
    
    # Create subdirectories
    (self.workspace_path / "cache").mkdir(exist_ok=True)
    (self.workspace_path / "temp").mkdir(exist_ok=True)
    (self.workspace_path / "output").mkdir(exist_ok=True)
```

### 6.3 Caching and Performance

Built-in caching support:

```python
def _cache_key(self, *args, **kwargs) -> str:
    """Generate cache key for function call"""
    import hashlib
    key_data = str(args) + str(sorted(kwargs.items()))
    return hashlib.md5(key_data.encode()).hexdigest()

def _cache_result(self, key: str, result: Any):
    """Cache function result"""
    cache_path = self.workspace_path / "cache" / f"{key}.json"
    with open(cache_path, 'w') as f:
        json.dump(result, f, indent=2, default=str)
```

## 7. Testing and Validation

### 7.1 Unit Testing

```python
def test_toolset():
    """Test toolset functionality"""
    from my_toolset.toolset import MyToolSet
    
    toolset = MyToolSet()
    result = toolset.my_function("test")
    
    assert result["status"] == "success"
    assert result["data"] == "test"
    print("✅ Toolset tests passed")
```

### 7.2 Integration Testing

```python
def test_integration():
    """Test integration with Pantheon"""
    import sys
    sys.path.append("./ext_toolsets")
    
    from ext_loader import ext_loader
    toolsets = ext_loader.discover_toolsets()
    
    assert "my_toolset" in toolsets
    print("✅ Integration tests passed")
```

This design provides a comprehensive, flexible, and maintainable external toolset system that integrates seamlessly with Pantheon while remaining independent and easy to use.