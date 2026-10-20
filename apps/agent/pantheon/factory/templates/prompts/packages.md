---
id: packages
name: Extended Tools
description: Using extended tools via packages API
---

# Extended Tools

Extended tools provide reusable capabilities beyond the core tools. They are designed for building complete programs rather than one-off operations.

**When to use extended tools:**
- Prefer writing Python scripts that combine multiple extended tools over calling individual tools separately
- Use packages for multi-step workflows: fetch data → process → analyze → output results
- Chain package methods together in a single script for complex operations
- Leverage packages for domain-specific logic (analytics, data processing, integrations)

> [!NOTE]
> MCP packages are automatically discovered on first access. You can optionally call `await pp.packages.list_packages()` to refresh or list all available packages.

## 1. Direct Tool: `search_tools`

Use the `search_tools` tool directly to discover available extended tools.

---

## 2. Python API (via Interpreter Tools)

Use the following tools to execute extended tools:

- **python_interpreter** - `from pantheon import packages as pp`
- **shell** - `python -c "from pantheon import packages as pp; ..."`
- **notebook** - Import in any cell

### Complete Usage Pattern

```python
import asyncio
from pantheon import packages as pp
# use async main() pattern only in python_interpreter tools, you can directly use await in notebooks
async def main():
    # Optional: List all available packages for discovery
    # packages = await pp.packages.list_packages()
    # print(f"Available packages: {[p['name'] for p in packages]}")
    
    # Call package methods directly - MCP packages auto-load on first access
    result = await pp.packages.<package>.<method>(arg1="value")
    # multi-step workflows: fetch data → process → analyze → output results
    return result

# Run the async main function
result = asyncio.run(main())
print(result)
```

### Discovery Functions

#### `await pp.packages.list_packages()` → `list[dict]`

List all available packages (including MCP servers).

**Returns:** List of package summaries:
```python
[
    {
        "name": str,           # Package name
        "description": str,    # Package description
        "methods": list[str],  # Available method names
        "status": str          # "ready" | "empty" | "error"
    }
]
```

#### `pp.packages.describe(name: str)` → `dict`

Get detailed metadata for a specific package.

**Returns:**
```python
{
    "success": bool,
    "package": {
        "name": str,
        "description": str,
        "methods": [
            {
                "name": str,
                "signature": str,   # e.g., "(data: list, format: str = 'json')"
                "params": dict,     # MCP only: {param_name: {type, description, required}}
                "doc": str,
                "async": bool
            }
        ]
    }
}
```

---

## Quick Reference

| Need | Method |
|------|--------|
| Discover (direct tool) | `search_tools("keyword")` |
| List all (via interpreter) | `await pp.packages.list_packages()` |
| Get API details | `pp.packages.describe("name")` |
| Call method | `await pp.packages.<pkg>.<method>(...)` |

