# External Toolset Agent Integration & Prompt Design

## 1. Agent Integration Architecture

Based on analysis of `pantheon/cli/core.py`, external modules need:
1. Agent class compatible interface
2. Auto-generated prompt instructions
3. Dynamic registration mechanism

## 2. Enhanced External Module Base Class

### `ext_toolsets/base.py` (Enhanced Version)
```python
"""Enhanced base class for external toolsets with Agent integration"""

import json
import inspect
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
from functools import wraps
from rich.console import Console

# Mock worker for compatibility with Agent.toolset()
class MockWorker:
    """Mock worker to simulate MagiqueWorker interface"""
    def __init__(self):
        self.functions = {}  # Store functions in MagiqueWorker format

def tool(func: Callable = None, **kwargs):
    """Enhanced tool decorator compatible with Pantheon"""
    def decorator(f):
        # Mark function as a tool
        f._is_tool = True
        f._tool_kwargs = kwargs
        
        # Generate OpenAI-compatible schema
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
        
        # Process parameters with OpenAI compatibility
        for param_name, param in sig.parameters.items():
            if param_name in ['self']:
                continue
                
            # Default to string for OpenAI API compatibility
            param_schema = {
                "type": "string",
                "description": f"Parameter {param_name}"
            }
            
            # Handle type annotations (limit to OpenAI compatible types)
            if param.annotation != inspect.Parameter.empty:
                if param.annotation == int:
                    param_schema["type"] = "integer"
                elif param.annotation == float:
                    param_schema["type"] = "number"
                elif param.annotation == bool:
                    param_schema["type"] = "boolean"
                # Note: Avoid Dict[str, str], List[str] etc. - use string and parse JSON
            
            schema["parameters"]["properties"][param_name] = param_schema
            
            if param.default == inspect.Parameter.empty:
                schema["parameters"]["required"].append(param_name)
        
        f._openai_schema = schema
        return f
    
    return decorator(func) if func else decorator

class ExternalToolSet:
    """Enhanced base class for external toolsets with Agent integration"""
    
    def __init__(self, name: str = None, **kwargs):
        self.name = name or self.__class__.__name__.lower().replace('toolset', '')
        self.console = Console()
        self.workspace_path = Path(kwargs.get('workspace_path', './workspace'))
        self.workspace_path.mkdir(exist_ok=True)
        
        # Load configuration
        self.config = self._load_config()
        
        # Create mock worker for Agent compatibility
        self.worker = MockWorker()
        self._register_tools()
        
        self.console.print(f"[green]Initialized {self.name} toolset with {len(self.worker.functions)} tools[/green]")
    
    def _load_config(self) -> Dict[str, Any]:
        """Load toolset configuration from config.json"""
        config_path = Path(__file__).parent / "config.json"
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self.console.print(f"[yellow]Warning: Failed to load config.json: {e}[/yellow]")
        
        return {
            "name": self.name, 
            "version": "1.0.0", 
            "description": f"External toolset: {self.name}",
            "capabilities": []
        }
    
    def _register_tools(self):
        """Register all @tool decorated methods with the mock worker"""
        for attr_name in dir(self):
            if not attr_name.startswith('_'):
                attr = getattr(self, attr_name)
                if callable(attr) and hasattr(attr, '_is_tool'):
                    # Register with mock worker in MagiqueWorker format
                    self.worker.functions[attr_name] = {
                        'func': attr,
                        'schema': getattr(attr, '_openai_schema', {}),
                        'description': attr.__doc__ or attr_name
                    }
    
    def get_prompt_instructions(self) -> str:
        """Generate prompt instructions for this toolset"""
        config = self.config
        tools_list = []
        
        for tool_name, tool_info in self.worker.functions.items():
            description = tool_info.get('description', tool_name)
            # Take first line of docstring for concise description
            description = description.split('\n')[0].strip()
            tools_list.append(f"  - {tool_name}: {description}")
        
        instructions = f"""
{self.name.upper()} TOOLSET ({config.get('version', '1.0.0')}):
- Description: {config.get('description', f'External toolset: {self.name}')}
- Author: {config.get('author', 'External Developer')}
- Capabilities: {', '.join(config.get('capabilities', ['general_purpose']))}

Available Tools:
{chr(10).join(tools_list)}

Usage Examples:
- "Use {self.name} to {config.get('description', 'perform tasks').lower()}"
- "Check {self.name} status and information"
- "Get help with {self.name} operations"

⚠️  IMPORTANT: All {self.name} tools return standardized format:
- "status": "success" or "error"
- "message": Human-readable message
- "data": Tool-specific results (optional)
- "recommendation": Next steps for errors (optional)
"""
        return instructions
```

## 3. Agent-Compatible Interface

### 3.1 MagiqueWorker Simulation

External toolsets need to simulate the MagiqueWorker interface that Pantheon Agent expects:

```python
class AgentCompatibleToolSet(ExternalToolSet):
    """Agent-compatible toolset with MagiqueWorker simulation"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Agent expects these attributes
        self.worker = self  # Self-reference for compatibility
        self.functions = {}  # Function registry
        
        # Register all tools
        self._discover_and_register_tools()
    
    def _discover_and_register_tools(self):
        """Discover and register all @tool decorated methods"""
        for attr_name in dir(self):
            if not attr_name.startswith('_'):
                attr = getattr(self, attr_name)
                if callable(attr) and hasattr(attr, '_is_tool'):
                    # Store in format expected by Agent
                    self.functions[attr_name] = attr
                    
                    # Add OpenAI schema if available
                    if hasattr(attr, '_openai_schema'):
                        attr._schema = attr._openai_schema
    
    def get_function_schemas(self) -> List[Dict[str, Any]]:
        """Get OpenAI-compatible function schemas for all tools"""
        schemas = []
        for func_name, func in self.functions.items():
            if hasattr(func, '_openai_schema'):
                schemas.append(func._openai_schema)
        return schemas
```

### 3.2 Dynamic Registration with Agent

```python
class ExternalToolsetsManager:
    """Manager for external toolsets integration with Agent"""
    
    def __init__(self, ext_dir: Path):
        self.ext_dir = Path(ext_dir)
        self.loaded_toolsets = {}
        self.console = Console()
    
    def register_toolsets_with_agent(self, agent, toolset_filter: Optional[List[str]] = None):
        """Register external toolsets with Pantheon Agent"""
        discovered = self.discover_toolsets()
        instructions_additions = []
        
        for toolset_name, toolset_info in discovered.items():
            # Apply filter if provided
            if toolset_filter and toolset_name not in toolset_filter:
                continue
            
            try:
                # Load and instantiate toolset
                toolset_class = self.load_toolset_class(toolset_name, toolset_info)
                if not toolset_class:
                    continue
                
                # Create instance
                toolset_instance = toolset_class()
                
                # Register with agent (Agent.toolset() method)
                agent.toolset(toolset_instance)
                
                # Generate instructions
                prompt_instructions = toolset_instance.get_prompt_instructions()
                instructions_additions.append(prompt_instructions)
                
                self.console.print(f"[green]✅ Registered external toolset: {toolset_name}[/green]")
                self.loaded_toolsets[toolset_name] = toolset_instance
                
            except Exception as e:
                self.console.print(f"[red]❌ Failed to register {toolset_name}: {e}[/red]")
        
        # Return combined instructions to add to agent
        return "\n\n".join(instructions_additions)
    
    def discover_toolsets(self) -> Dict[str, Dict[str, Any]]:
        """Discover available external toolsets"""
        toolsets = {}
        
        if not self.ext_dir.exists():
            return toolsets
        
        for item in self.ext_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Check for required files
                if (item / '__init__.py').exists() and (item / 'toolset.py').exists():
                    config = {}
                    config_path = item / 'config.json'
                    if config_path.exists():
                        try:
                            with open(config_path, 'r', encoding='utf-8') as f:
                                config = json.load(f)
                        except Exception as e:
                            self.console.print(f"[yellow]Warning: Failed to load config for {item.name}: {e}[/yellow]")
                    
                    toolsets[item.name] = {
                        'path': item,
                        'config': config,
                        'module_name': f"ext_toolsets.{item.name}"
                    }
        
        return toolsets
    
    def load_toolset_class(self, name: str, toolset_info: Dict[str, Any]):
        """Load toolset class from module"""
        try:
            import importlib
            module_name = toolset_info['module_name']
            
            # Import the module
            module = importlib.import_module(module_name)
            
            # Find the toolset class (should inherit from ExternalToolSet)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    hasattr(attr, '__bases__') and
                    any('ExternalToolSet' in str(base) for base in attr.__bases__)):
                    return attr
                    
        except Exception as e:
            self.console.print(f"[red]Error importing {name}: {e}[/red]")
        
        return None
```

## 4. Prompt Generation System

### 4.1 Automatic Prompt Generation

```python
def generate_toolset_instructions(toolsets: Dict[str, Any]) -> str:
    """Generate comprehensive instructions for all external toolsets"""
    
    if not toolsets:
        return ""
    
    instructions = "\n\n# EXTERNAL TOOLSETS\n\n"
    instructions += "You have access to the following external toolsets:\n\n"
    
    for name, toolset_instance in toolsets.items():
        config = toolset_instance.config
        
        instructions += f"## {name.upper()} TOOLSET\n"
        instructions += f"- **Description**: {config.get('description', f'External toolset: {name}')}\n"
        instructions += f"- **Version**: {config.get('version', '1.0.0')}\n"
        instructions += f"- **Author**: {config.get('author', 'External Developer')}\n"
        
        # Add capabilities
        capabilities = config.get('capabilities', [])
        if capabilities:
            instructions += f"- **Capabilities**: {', '.join(capabilities)}\n"
        
        # Add available tools
        tools = []
        for attr_name in dir(toolset_instance):
            attr = getattr(toolset_instance, attr_name)
            if hasattr(attr, '_is_tool'):
                doc = attr.__doc__ or attr_name
                first_line = doc.split('\n')[0].strip()
                tools.append(f"  - **{attr_name}**: {first_line}")
        
        if tools:
            instructions += f"- **Available Tools**:\n" + '\n'.join(tools) + "\n"
        
        # Add usage examples
        instructions += f"- **Usage Examples**:\n"
        instructions += f"  - \"Use {name} to {config.get('description', 'perform tasks').lower()}\"\n"
        instructions += f"  - \"Check {name} status\"\n"
        instructions += f"  - \"Get help with {name} operations\"\n\n"
        
        # Add important notes
        instructions += f"⚠️  **{name.upper()} IMPORTANT NOTES**:\n"
        instructions += f"- All tools return standardized format: {{\"status\": \"success/error\", \"message\": \"...\", \"data\": {{...}}}}\n"
        instructions += f"- Check tool descriptions and parameters before use\n"
        instructions += f"- Handle errors gracefully with user-friendly explanations\n\n"
    
    return instructions
```

### 4.2 Dynamic Tool Discovery

```python
def discover_tools_for_prompt(toolset_instance) -> List[Dict[str, Any]]:
    """Discover tools and generate prompt-friendly descriptions"""
    tools_info = []
    
    for attr_name in dir(toolset_instance):
        if not attr_name.startswith('_'):
            attr = getattr(toolset_instance, attr_name)
            if callable(attr) and hasattr(attr, '_is_tool'):
                # Extract information for prompt
                doc = attr.__doc__ or f"Execute {attr_name} operation"
                
                # Parse docstring for better description
                lines = doc.strip().split('\n')
                description = lines[0].strip()
                
                # Extract parameters info if available
                sig = inspect.signature(attr)
                params = []
                for param_name, param in sig.parameters.items():
                    if param_name != 'self':
                        param_type = "string"
                        if param.annotation != inspect.Parameter.empty:
                            if param.annotation == int:
                                param_type = "integer"
                            elif param.annotation == bool:
                                param_type = "boolean"
                            elif param.annotation == float:
                                param_type = "number"
                        
                        params.append({
                            "name": param_name,
                            "type": param_type,
                            "required": param.default == inspect.Parameter.empty
                        })
                
                tools_info.append({
                    "name": attr_name,
                    "description": description,
                    "parameters": params,
                    "example": f"Use {toolset_instance.name} {attr_name} to {description.lower()}"
                })
    
    return tools_info
```

## 5. Integration with CLI Core

### 5.1 Modified CLI Integration

In `pantheon/cli/core.py`, the integration should be:

```python
# Load external toolsets
ext_instructions = ""
ext_loader = load_external_toolsets(ext_dir)

if ext_loader:
    print(f"🔌 Checking for external toolsets in {ext_dir}...")
    
    # Parse toolset list if provided
    toolset_list = None
    if ext_toolsets:
        toolset_list = [name.strip() for name in ext_toolsets.split(',')]
        print(f"📋 Loading specific toolsets: {toolset_list}")
    
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

### 5.2 Error Handling and Validation

```python
def validate_external_toolset(toolset_path: Path) -> Dict[str, Any]:
    """Validate external toolset structure and compatibility"""
    validation_result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "info": {}
    }
    
    # Check required files
    required_files = ['__init__.py', 'toolset.py']
    for file_name in required_files:
        if not (toolset_path / file_name).exists():
            validation_result["errors"].append(f"Missing required file: {file_name}")
            validation_result["valid"] = False
    
    # Check config.json
    config_path = toolset_path / 'config.json'
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                validation_result["info"]["config"] = config
        except json.JSONDecodeError as e:
            validation_result["warnings"].append(f"Invalid JSON in config.json: {e}")
    else:
        validation_result["warnings"].append("No config.json found, using defaults")
    
    # Try to import and validate toolset class
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("toolset", toolset_path / "toolset.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Find toolset class
        toolset_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and 
                hasattr(attr, '__bases__') and
                any('ExternalToolSet' in str(base) for base in attr.__bases__)):
                toolset_class = attr
                break
        
        if toolset_class:
            # Count tools
            instance = toolset_class()
            tool_count = len([m for m in dir(instance) if hasattr(getattr(instance, m), '_is_tool')])
            validation_result["info"]["tools_count"] = tool_count
        else:
            validation_result["errors"].append("No ExternalToolSet class found")
            validation_result["valid"] = False
            
    except Exception as e:
        validation_result["errors"].append(f"Failed to load toolset module: {e}")
        validation_result["valid"] = False
    
    return validation_result
```

## 6. Advanced Features

### 6.1 Hot Reloading

```python
def reload_external_toolset(agent, toolset_name: str):
    """Hot reload an external toolset"""
    # Remove existing toolset
    if hasattr(agent, '_external_toolsets') and toolset_name in agent._external_toolsets:
        old_toolset = agent._external_toolsets[toolset_name]
        # Remove from agent's toolset registry
        if hasattr(agent, 'toolsets') and old_toolset in agent.toolsets:
            agent.toolsets.remove(old_toolset)
    
    # Reload module
    import importlib
    module_name = f"ext_toolsets.{toolset_name}"
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    
    # Re-register
    ext_loader = ExternalToolsetsManager(Path("./ext_toolsets"))
    ext_loader.register_toolsets_with_agent(agent, [toolset_name])
```

### 6.2 Performance Monitoring

```python
def monitor_external_toolset_performance(toolset_instance):
    """Monitor performance of external toolset tools"""
    import time
    import functools
    
    def performance_wrapper(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                success = True
            except Exception as e:
                result = {"status": "error", "message": str(e)}
                success = False
            finally:
                end_time = time.time()
                duration = end_time - start_time
                
                # Log performance
                toolset_instance.console.print(f"[dim]🔧 {func.__name__}: {duration:.3f}s ({'✅' if success else '❌'})[/dim]")
            
            return result
        return wrapper
    
    # Wrap all tools with performance monitoring
    for attr_name in dir(toolset_instance):
        attr = getattr(toolset_instance, attr_name)
        if hasattr(attr, '_is_tool'):
            setattr(toolset_instance, attr_name, performance_wrapper(attr))
```

## 7. Best Practices

### 7.1 Tool Design Guidelines

- **Consistent Return Format**: All tools should return `{"status": "success/error", "message": "...", "data": {...}}`
- **Rich Console Output**: Use `self.console.print()` for user feedback
- **Error Handling**: Comprehensive error handling with actionable recommendations
- **OpenAI Compatibility**: Use simple parameter types (str, int, float, bool)
- **Documentation**: Complete docstrings with examples
- **Validation**: Input validation with helpful error messages

### 7.2 Integration Guidelines

- **Agent Compatibility**: Implement mock worker interface
- **Prompt Integration**: Generate clear, concise tool descriptions
- **Configuration Management**: Support runtime configuration
- **Testing**: Include validation and integration tests
- **Performance**: Monitor and optimize tool performance

This integration system provides seamless compatibility with Pantheon Agent while maintaining the flexibility and independence of external toolsets.