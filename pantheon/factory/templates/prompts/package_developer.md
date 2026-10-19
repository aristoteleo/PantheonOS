---
id: package_developer
name: Package Developer Guide
description: Comprehensive guide for developing Pantheon packages
---

# Package Developer Guide

This guide covers creating and maintaining reusable Pantheon packages under `${{pantheon_dir}}/packages/`.

## Package Structure

```
${{pantheon_dir}}/packages/
├── my_package/
│   ├── __init__.py      # Optional, but recommended
│   ├── core.py          # Main implementation
│   └── utils.py         # Helper modules
```

> **Note**: `__init__.py` is optional. If missing, the first `.py` file alphabetically becomes the entry point.

## Creating a Basic Package

### Standard Class Package

```python
# ${{pantheon_dir}}/packages/sales_report/__init__.py

class SalesReportPackage:
    """Sales analytics and reporting utilities.
    
    Provides methods for generating sales reports, analyzing trends,
    and exporting data in various formats.
    """

    def generate(self, date: str, region: str | None = None) -> dict:
        """Generate a sales report for the specified date.
        
        Args:
            date: Report date in YYYY-MM-DD format.
            region: Optional region filter (e.g., "APAC", "EMEA").
            
        Returns:
            dict: Report data with keys: summary, details, totals.
        """
        # Implementation here
        return {"summary": "...", "details": [...], "totals": {...}}
    
    def export_csv(self, data: dict, output_path: str) -> str:
        """Export report data to CSV format.
        
        Args:
            data: Report data from generate().
            output_path: Destination file path.
            
        Returns:
            str: Path to the created CSV file.
        """
        # Implementation here
        return output_path
```

**Key Rules**:
- Public methods (no leading `_`) with docstrings are auto-discovered
- Use type hints for clear signatures
- Write comprehensive docstrings (they become the API documentation)

### ToolSet-Based Package (Advanced)

For packages requiring context access, async support, or @tool decoration:

```python
# ${{pantheon_dir}}/packages/data_ops/__init__.py

from pantheonos.toolset import ToolSet, tool

class DataOpsToolSet(ToolSet):
    """Data operations toolset with context-aware methods."""

    @tool
    async def fetch_data(self, source: str, query: str) -> dict:
        """Fetch data from a source with the given query.
        
        Args:
            source: Data source identifier.
            query: Query string.
            
        Returns:
            dict: {success: bool, data: list, error: str | None}
        """
        # Access context if needed
        ctx = self.get_context()
        client_id = ctx.get("client_id") if ctx else None
        
        # Implementation
        return {"success": True, "data": [...]}
    
    @tool(exclude=True)  # Not exposed to LLM
    async def internal_cleanup(self) -> None:
        """Internal maintenance method."""
        pass
```

**ToolSet Features**:
- `@tool` decorator for method registration
- `@tool(exclude=True)` hides from LLM discovery
- `self.get_context()` for runtime metadata
- Full async/await support

## Docstring Best Practices

Docstrings are parsed to generate the API. Follow these rules:

```python
def method_name(self, param1: str, param2: int = 10) -> dict:
    """One-line summary of the method.
    
    Detailed description if needed. Explain behavior,
    edge cases, and any important notes.
    
    Args:
        param1: Description of param1.
        param2: Description with default behavior.
        
    Returns:
        dict: Structure description.
            - key1: What this key contains.
            - key2: What this key contains.
            
    Raises:
        ValueError: When param1 is empty.
        
    Examples:
        >>> result = package.method_name("test")
        >>> print(result["key1"])
    """
```

## Testing Packages

Before finalizing, test in Python interpreter:

```python
# Quick test
from pantheon import packages as pp

# Check package is discovered (async - auto-discovers MCP servers)
packages = await pp.packages.list_packages()

# Get full metadata
pp.packages.describe("my_package")

# Test methods
result = pp.packages.my_package.generate(date="2025-01-01")
print(result)
```

## Package Update Workflow

1. **Edit files** under `${{pantheon_dir}}/packages/<name>/`
2. **Reload** (automatic on next import, or force with `pp.packages.reload("<name>")`)
3. **Test** changes in interpreter
4. **Verify** with `describe()` that new methods appear

## Common Patterns

### Async Method with Context

```python
@tool
async def process_with_context(self, data: list) -> dict:
    """Process data with agent context."""
    ctx = self.get_context()
    
    # Use call_agent for LLM sub-calls
    if ctx and ctx.get("_call_agent"):
        response = await ctx.call_agent(
            messages=[{"role": "user", "content": f"Analyze: {data}"}],
            model="gpt-4o-mini"
        )
    
    return {"success": True, "analysis": response}
```

### Error Handling Pattern

```python
def safe_operation(self, input_data: str) -> dict:
    """Perform operation with standardized error handling.
    
    Returns:
        dict: {success: bool, result: Any, error: str | None}
    """
    try:
        result = self._internal_process(input_data)
        return {"success": True, "result": result, "error": None}
    except ValueError as e:
        return {"success": False, "result": None, "error": str(e)}
```

## Checklist

- [ ] Package has clear, single responsibility
- [ ] All public methods have comprehensive docstrings
- [ ] Type hints on all parameters and return values
- [ ] Tested in Python interpreter
- [ ] Error cases return structured responses
- [ ] Async methods use `@tool` decorator when in ToolSet
