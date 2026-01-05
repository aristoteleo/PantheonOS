"""Unified package management toolset for Pantheon packages.

This module provides the `PackageToolSet` class for discovering and searching
packages within a Pantheon workspace, supporting both keyword-based and
LLM-based semantic search capabilities.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pantheon.internal.package_runtime import get_package_manager
from pantheon.toolset import ToolSet, tool, get_current_context_variables
from pantheon.utils.log import logger

# Default low-cost model for semantic search (uses tag system)
DEFAULT_SEMANTIC_MODEL = "low"


class PackageToolSet(ToolSet):
    """Expose read-only management functions for packages via tools.

    This toolset provides package and tool discovery capabilities for
    Pantheon workspaces:

    - **search_packages** (UI/API): List packages with keyword filtering.
      Not exposed to LLM.

    - **search_tools** (LLM): Semantic search for callable methods.
      Returns full signatures for direct invocation.

    The semantic search uses `call_agent` mechanism to delegate to a
    low-cost LLM model (uses "low" tag) for intelligent matching.

    Future Improvements:
        - Embedding-based pre-filtering for large package collections
        - Hybrid approach: Embedding recall + LLM rerank

    Args:
        name: ToolSet identifier name.
        workdir: Workspace root directory. Packages are stored in
            `.pantheon/packages` under this directory.
        enable_semantic_search: Whether to enable LLM-based semantic
            search by default for search_tools. Defaults to True.
        semantic_model: Model name or tag for semantic search. Defaults to
            "low" tag for cost efficiency.
    """

    def __init__(
        self,
        name: str,
        workdir: str | Path | None = None,
        enable_semantic_search: bool = True,
        semantic_model: str = DEFAULT_SEMANTIC_MODEL,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        
        # Use global Settings (PROJECT_ROOT based) to avoid path doubling
        # when Endpoint passes its workspace_path as workdir
        from pantheon.settings import get_settings
        settings = get_settings()
        packages_path = settings.packages_dir
        
        packages_path.mkdir(parents=True, exist_ok=True)
        self.manager = get_package_manager(packages_path)
        self.enable_semantic_search = enable_semantic_search
        self.semantic_model = semantic_model

    def _keyword_search(
        self,
        query: str,
        items: list[dict],
        fields: list[str],
    ) -> list[dict]:
        """Generic keyword-based substring matching.

        Args:
            query: Search query string.
            items: List of dictionaries to search.
            fields: List of field names to search in each item.

        Returns:
            Filtered list of items matching the query.
        """
        q = query.lower()
        results = []
        for item in items:
            haystacks = []
            for field in fields:
                value = item.get(field)
                if isinstance(value, list):
                    haystacks.append(" ".join(value))
                elif value:
                    haystacks.append(str(value))
            if any(q in hay.lower() for hay in haystacks):
                results.append(item)
        return results

    def _parse_llm_response(self, response: str) -> list[str]:
        """Parse LLM response to extract matched identifiers.

        Args:
            response: Raw LLM response string.

        Returns:
            List of identifiers extracted from the response.
        """
        try:
            # Try to extract JSON array from response (handles markdown code blocks)
            json_match = re.search(
                r'\[\s*(?:"[^"]*"\s*,?\s*)*\]',
                response,
                re.DOTALL,
            )
            if json_match:
                return json.loads(json_match.group())

            # Try direct JSON parse
            result = json.loads(response)
            if isinstance(result, list):
                return result
            return []
        except (json.JSONDecodeError, AttributeError):
            logger.warning(f"Failed to parse LLM response: {response[:100]}...")
            return []

    @tool(exclude=True)
    async def search_packages(
        self,
        query: str | None = None,
    ) -> dict:
        """List and search available packages (for UI/API use).

        Returns package-level metadata including name, description,
        available methods, and status. Not exposed to LLM - use
        search_tools for LLM-based tool discovery.

        Args:
            query: Optional keyword filter. If provided, filters packages
                by substring matching on name, description, and method names.
                If None, returns all available packages.

        Returns:
            dict: {
                "success": bool,
                "packages": [
                    {
                        "name": str,
                        "description": str | None,
                        "methods": list[str],
                        "status": str,
                        "origin": "user" | "system",
                        "path": str | None,
                        ...
                    },
                    ...
                ],
                "error": str  # only present if success is False
            }

        Examples:
            # List all packages
            >>> await search_packages()

            # Filter by keyword
            >>> await search_packages("data")
        """
        try:
            packages = await self.manager.list_packages()

            if not query:
                return {"success": True, "packages": packages}

            # Simple keyword filtering
            results = self._keyword_search(query, packages, ["name", "description", "methods"])
            return {"success": True, "packages": results}

        except Exception as exc:
            logger.exception(f"search_packages failed: {exc}")
            return {"success": False, "error": str(exc)}

    async def _get_all_tools(self) -> list[dict]:
        """Get all tools from all packages with full metadata.

        Returns:
            List of tool dictionaries with package, method, signature, params, doc, origin.
        """
        tools = []
        for pkg in await self.manager.list_packages():
            pkg_name = pkg.get("name", "unknown")
            pkg_origin = pkg.get("origin", "user")
            try:
                detail = self.manager.describe_package(pkg_name)
                if not detail.get("success"):
                    continue
                pkg_info = detail.get("package", {})
                for method in pkg_info.get("methods", []):
                    is_async = method.get("async", False)
                    method_path = f"packages.{pkg_name}.{method.get('name')}"
                    call_example = f"await {method_path}(...)" if is_async else f"{method_path}(...)"
                    tools.append({
                        "package": pkg_name,
                        "method": method.get("name"),
                        "signature": method.get("signature", "()"),
                        "params": method.get("params"),  # May be None for non-MCP
                        "doc": method.get("doc") or "",
                        "async": is_async,
                        "call_example": call_example,
                        "origin": pkg_origin,
                    })
            except Exception as e:
                logger.warning(f"Failed to get tools from package {pkg_name}: {e}")
        return tools

    def _format_tools_for_llm(
        self,
        tools: list[dict],
        max_doc_len: int = 100,
    ) -> str:
        """Format tool information for LLM consumption.

        Args:
            tools: List of tool metadata dictionaries.
            max_doc_len: Maximum doc length to include.

        Returns:
            Formatted string representation of tools.
        """
        lines = []
        for t in tools:
            doc = (t.get("doc") or "")[:max_doc_len]
            async_marker = "[async] " if t.get("async") else ""
            lines.append(
                f"- {t['package']}.{t['method']}{t['signature']}: {async_marker}{doc}"
            )
        return "\n".join(lines)


    async def _semantic_search_tools(
        self,
        query: str,
        tools: list[dict],
        use_context: bool = False,
    ) -> tuple[list[dict], dict | None]:
        """Use LLM to perform semantic search on tools.

        Args:
            query: Natural language search query.
            tools: List of all available tools.
            use_context: Whether to include conversation history for
                context-aware search.

        Returns:
            Tuple of (matching_tools, metadata).
            metadata contains cost info from nested agent call if available.
        """
        ctx = get_current_context_variables()
        if ctx is None or not ctx.get("_call_agent"):
            logger.debug("call_agent not available, falling back to keyword search")
            return self._keyword_search(query, tools, ["package", "method", "doc"]), None

        tools_info = self._format_tools_for_llm(tools)

        prompt = f"""You are a tool search assistant. Find the most relevant tools based on the user's query.

## Available Tools

{tools_info}

## User Query

{query}

## Task

Return ALL tools that match the user's query. Consider:
- **Package names**: If the query matches or contains a package name, include ALL tools from that package
- **Method names**: If the query matches a method name, include that tool
- **Descriptions**: If the query semantically relates to a tool's description, include it
- Be INCLUSIVE rather than exclusive - when in doubt, include the tool

## Output Format

Output a JSON array of tool identifiers (package.method):
["package1.method1", "package2.method2"]

IMPORTANT: Never return an empty array if the query matches a package or method name.
"""

        try:
            response = await ctx.call_agent(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=(
                    "You are a precise tool search assistant. "
                    "Always respond with valid JSON only, no explanations."
                ),
                model=self.semantic_model,
                use_memory=use_context,
            )

            # Extract metadata and response content from dict
            # call_agent always returns {"success": True, "response": ..., "_metadata": {...}}
            metadata = response.get("_metadata")
            response_content = response.get("response", "")

            # Parse response
            matched_ids = self._parse_llm_response(response_content)

            if not matched_ids:
                return [], metadata

            # Filter tools by matched identifiers
            id_set = set(mid.lower() for mid in matched_ids)
            results = [
                t for t in tools
                if f"{t['package']}.{t['method']}".lower() in id_set
            ]

            logger.debug(f"Semantic search matched {len(results)} tools for: {query}")
            return results, metadata

        except Exception as e:
            logger.warning(f"Semantic tool search failed, falling back to keyword: {e}")
            return self._keyword_search(query, tools, ["package", "method", "doc"]), None


    @tool
    async def search_tools(
        self,
        query: str | None = None,
        semantic: bool | None = None,
        top_k: int | None = None,
        use_context: bool = False,
    ) -> dict:
        """Search for extended tools available.

        Extended tools provide reusable capabilities beyond the core tools. They are designed for building complete programs rather than one-off operations.

        **When to use extended tools:**
        - Prefer writing Python scripts that combine multiple extended tools over calling individual tools separately
        - Use packages for multi-step workflows: fetch data → process → analyze → output results
        - Chain package methods together in a single script for complex operations
        - Leverage packages for domain-specific logic (analytics, data processing, integrations)


        **Usage Pattern:**
        ```python
        from pantheon import packages as pp

        # 1. Call directly (no explicit discovery needed)
        # Interactive (Notebook / IPython):
        result = await pp.packages.<pkg>.<method>(...)
        result = await pp.packages.<pkg>.<method>(...)

        # 2. Standalone Script:
        import asyncio
        async def main():
            return await pp.packages.<pkg>.<method>(...)
        result = asyncio.run(main())
        ```
        *Note: Replace `<pkg>` and `<method>` with names from search results.*

        ### Discovery Functions using package API

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

        Args:
            query: Search query describing what tool/functionality you need.
                If None or empty, returns all available tools.
                Use natural language like "convert CSV to Excel".
            semantic: Explicitly control search mode.
                - True: Use LLM-based semantic search.
                - False: Use keyword substring matching.
                - None (default): Use the toolset's default setting.
            top_k: Maximum number of tools to return. None means no limit.
            use_context: Whether to use conversation history as context
                for semantic search. Useful when the query references
                previous discussion (e.g., "find a tool similar to what
                we discussed"). Defaults to False.

        Returns:
            dict: {
                "success": bool,
                "tools": [
                    {
                        "package": str,         # Package name
                        "method": str,          # Method name
                        "signature": str,       # Full signature with params
                        "doc": str,             # Method documentation
                        "async": bool,          # Whether method is async
                        "call_example": str     # Example: packages.pkg.method(...)
                    },
                    ...
                ],
                "error": str  # only present if success is False
            }

        Examples:
            # Find tools for data conversion
            >>> search_tools("convert CSV to Excel")

            # List all available tools
            >>> search_tools()

            # Keyword search with limit
            >>> search_tools("export", semantic=False, top_k=5)

            # Context-aware search (uses conversation history)
            >>> search_tools("find something similar to what we discussed", use_context=True)
        """
        try:
            tools = await self._get_all_tools()
            metadata = None

            if not query:
                result_tools = tools
            else:
                use_semantic = (
                    semantic if semantic is not None else self.enable_semantic_search
                )

                if use_semantic:
                    result_tools, metadata = await self._semantic_search_tools(
                        query, tools, use_context=use_context
                    )
                else:
                    result_tools = self._keyword_search(query, tools, ["package", "method", "doc"])

            # Apply top_k limit
            if top_k is not None and top_k > 0:
                result_tools = result_tools[:top_k]

            result = {"success": True, "tools": result_tools}
            # Merge cost metadata from nested agent call
            if metadata:
                result.setdefault("_metadata", {}).update(metadata)
            return result

        except Exception as exc:
            logger.exception(f"search_tools failed: {exc}")
            return {"success": False, "error": str(exc)}


__all__ = ["PackageToolSet"]
