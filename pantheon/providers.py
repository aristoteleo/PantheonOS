"""Tool Providers for Pantheon Agents

This module provides implementations for different tool sources:
- MCPProvider: For Model Context Protocol servers
- LocalProvider: For local ToolSet instances (in-memory calls)
- ToolSetProvider: For remote Pantheon ToolSets (with session isolation)
"""

import asyncio
import json
from threading import Lock
from typing import Any, Callable, Optional

from fastmcp import Client
from fastmcp.client.messages import MessageHandler

from .agent import ToolInfo, ToolProvider
from .endpoint.toolset_proxy import ToolsetProxy
from .utils.log import logger


# Constants for special parameters
_CTX_VARS_NAME = "context_variables"
_SKIP_PARAMS = [_CTX_VARS_NAME, "_call_agent"]


class MCPProvider(ToolProvider):
    """Tool Provider for Model Context Protocol (MCP) servers.

    Uses singleton pattern per URI and passive TTL caching for tools.
    Configuration is read from settings module automatically.
    """

    # Singleton instances per (URI, filter_prefix)
    _instances: dict[tuple[str, str | None], "MCPProvider"] = {}
    _instances_lock = Lock()

    def __init__(self, uri: str, filter_prefix: str | None = None):
        """Initialize MCPProvider (use get_instance() for singleton access).

        Args:
            uri: MCP server URI
            filter_prefix: Optional prefix to filter tools (e.g., "context7")
        """
        self.uri = uri
        self.filter_prefix = filter_prefix
        self._client: Optional[Client] = None
        self._tools_cache: Optional[list[ToolInfo]] = None
        self._cache_time: float = 0

    @classmethod
    def get_instance(cls, uri: str, filter_prefix: str | None = None) -> "MCPProvider":
        """Get or create a singleton MCPProvider.

        Args:
            uri: MCP server URI
            filter_prefix: Optional prefix to filter tools

        Returns:
            MCPProvider instance (singleton per uri+filter combination)
        """
        key = (uri, filter_prefix)
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(uri, filter_prefix)
                prefix_info = f" (filter: {filter_prefix})" if filter_prefix else ""
                logger.debug(f"Created MCPProvider singleton for {uri}{prefix_info}")
            return cls._instances[key]

    @classmethod
    def clear_instances(cls):
        """Clear all singleton instances (for testing)."""
        with cls._instances_lock:
            cls._instances.clear()

    def invalidate_cache(self):
        """Invalidate tools cache to force refresh on next list_tools call."""
        self._tools_cache = None
        self._cache_time = 0
        logger.debug(f"MCPProvider '{self.uri}': cache invalidated")

    @property
    def model(self) -> str | None:
        """Get the sampling model from settings."""
        from pantheon.settings import get_settings

        return get_settings().get_mcp_config().get("sampling_model", "normal")

    @property
    def cache_ttl_seconds(self) -> int:
        """Get the cache TTL from settings."""
        from pantheon.settings import get_settings

        return get_settings().get_mcp_config().get("cache_ttl", 60)

    @property
    def config(self):
        """Compatibility property - returns a minimal config-like object."""

        class _MinimalConfig:
            def __init__(self, uri):
                self.name = uri.split("/")[-1] if uri else "unknown"
                self.uri = uri

        return _MinimalConfig(self.uri)

    async def _sampling_handler(
        self,
        messages: list,
        params,
        context,
    ) -> str:
        """Handle MCP sampling requests"""
        try:
            from .agent import Agent  # Import here to avoid circular imports

            # Extract system prompt from params
            system_prompt = (
                getattr(params, "systemPrompt", None) or "You are a helpful assistant."
            )

            # Convert SamplingMessage objects to conversation
            conversation = []
            for message in messages:
                if hasattr(message.content, "text"):
                    text = message.content.text
                else:
                    text = str(message.content)
                conversation.append({"role": message.role, "content": text})

            # Extract last user message (the actual query)
            user_query = None
            for message in reversed(messages):
                if message.role == "user":
                    if hasattr(message.content, "text"):
                        user_query = message.content.text
                    else:
                        user_query = str(message.content)
                    break

            if not user_query:
                provider_name = self.config.name if self.config else "unknown"
                logger.warning(
                    f"MCPProvider '{provider_name}': No user query in sampling request"
                )
                return "No user query found in sampling request."

            # Create temporary Agent for sampling
            request_id = getattr(context, "request_id", "unknown")
            agent = Agent(
                name=f"mcp_sampler_{request_id}",
                instructions=system_prompt,
                model=self.model,
            )

            provider_name = self.config.name if self.config else "unknown"
            logger.debug(
                f"MCPProvider '{provider_name}': Sampling request - "
                f"Query: {user_query[:50]}..."
            )

            # Run agent with the user query
            result = await agent.run(user_query)

            # Extract response text
            if isinstance(result, str):
                response_text = result
            elif isinstance(result, dict):
                response_text = (
                    result.get("response") or result.get("text") or str(result)
                )
            else:
                response_text = str(result)

            return response_text

        except Exception as e:
            provider_name = self.config.name if self.config else "unknown"
            logger.error(
                f"MCPProvider '{provider_name}': Error handling sampling request: {e}",
                exc_info=True,
            )
            # Return error message instead of raising
            return f"Error during sampling: {str(e)}"

    async def _get_client(self) -> Client:
        """Get or create the MCP client."""
        if self._client is not None:
            return self._client

        if not self.uri:
            raise ValueError("MCPProvider: URI not configured")

        # Create new client with sampling handler
        logger.debug(f"Creating MCP client for {self.uri}")
        self._client = Client(
            self.uri,
            name="pantheon-agent",
            sampling_handler=self._sampling_handler,
        )

        return self._client

    async def initialize(self):
        """Initialize the provider (creates client if needed)."""
        try:
            await self._get_client()
            logger.debug(f"MCPProvider '{self.uri}' initialized")
        except Exception as e:
            logger.error(f"Failed to initialize MCPProvider: {e}")
            raise

    async def list_tools(self) -> list[ToolInfo]:
        """List all available tools from the MCP server.

        Uses passive TTL caching - checks cache validity on each call.
        """
        import time

        now = time.time()

        # Check if cache is valid (not expired)
        if (
            self._tools_cache is not None
            and (now - self._cache_time) < self.cache_ttl_seconds
        ):
            return self._tools_cache

        # Cache expired or empty - refresh
        if not self._client:
            await self.initialize()

        try:
            async with self._client:
                tools_response = await self._client.list_tools()

                tool_infos = []
                for tool in tools_response:
                    # Apply filter if specified
                    if self.filter_prefix and not tool.name.startswith(f"{self.filter_prefix}_"):
                        continue
                    
                    params = tool.inputSchema if hasattr(tool, "inputSchema") else {}

                    function_schema = {
                        "name": tool.name,
                        "description": tool.description,
                        "strict": False,
                        "parameters": params,
                    }

                    tool_info = ToolInfo(
                        name=tool.name,
                        description=tool.description,
                        inputSchema=function_schema,
                    )
                    tool_infos.append(tool_info)

                # Update cache with timestamp
                self._tools_cache = tool_infos
                self._cache_time = now
                prefix_info = f" (filtered by '{self.filter_prefix}')" if self.filter_prefix else ""
                logger.debug(
                    f"MCPProvider '{self.uri}': cached {len(tool_infos)} tools{prefix_info}"
                )

                return tool_infos

        except Exception as e:
            logger.error(f"Failed to list tools from MCP server '{self.uri}': {e}")
            raise

    async def call_tool(self, name: str, args: dict) -> Any:
        """Call a tool on the MCP server."""
        if not self._client:
            await self.initialize()

        try:
            logger.debug(f"Calling MCP tool '{name}' on '{self.uri}'")

            # Use async with to establish connection for this operation
            async with self._client:
                result = await self._client.call_tool(name, args)

                # Import unwrap utility
                from pantheon.utils.misc import unwrap_single_layer

                # Priority 1: .structured_content (raw MCP JSON)
                # Universal, portable format compatible with all systems
                if (
                    hasattr(result, "structured_content")
                    and result.structured_content is not None
                ):
                    logger.debug(
                        f"MCPProvider '{self.uri}': Extracted result via .structured_content"
                    )
                    extracted = result.structured_content
                    return unwrap_single_layer(extracted)

                # Priority 2: Parse .content[0].text (fallback for unstructured)
                if hasattr(result, "content") and result.content:
                    if len(result.content) > 0:
                        content_block = result.content[0]
                        if hasattr(content_block, "text"):
                            text = content_block.text
                            # Try to parse as JSON first
                            try:
                                parsed = json.loads(text)
                                logger.debug(
                                    f"MCPProvider '{self.uri}': Extracted JSON from content text"
                                )
                                return unwrap_single_layer(parsed)
                            except (json.JSONDecodeError, TypeError):
                                # Not JSON, return text as-is
                                logger.debug(
                                    f"MCPProvider '{self.uri}': Extracted plain text from content"
                                )
                                return text

                # Priority 3: .data (Python objects - fallback when JSON unavailable)
                if hasattr(result, "data") and result.data is not None:
                    logger.debug(
                        f"MCPProvider '{self.uri}': Extracted result via .data "
                        f"(type: {type(result.data).__name__})"
                    )
                    return unwrap_single_layer(result.data)

                # Fallback: Return entire result
                logger.warning(
                    f"MCPProvider '{self.uri}': Could not extract result from CallToolResult "
                    f"for tool '{name}'."
                )
                return result

        except Exception as e:
            logger.error(f"Failed to call MCP tool '{name}': {e}")
            raise

    async def shutdown(self):
        """Clean up provider resources."""
        if self._client:
            try:
                self._tools_cache = None
                self._cache_time = 0
                logger.debug(f"MCPProvider '{self.uri}' shut down")
            except Exception as e:
                logger.error(f"Error shutting down MCPProvider '{self.uri}': {e}")


class LocalProvider(ToolProvider):
    """Tool Provider for local ToolSet instances (in-memory calls)"""

    def __init__(self, toolset: "ToolSet"):
        """Initialize LocalProvider with a ToolSet instance

        Args:
            toolset: A ToolSet instance to call directly in-memory
        """
        self.toolset = toolset

        if hasattr(self.toolset, "streaming_mode"):
            self.toolset.streaming_mode = "local"
        self._tools_cache: Optional[list[ToolInfo]] = None
        self._tool_descriptions: dict[
            str, dict
        ] = {}  # name -> tool_desc for parameter filtering

    @property
    def toolset_name(self):
        return self.toolset.toolset_name

    async def initialize(self):
        """Initialize the provider"""
        try:
            # Ensure toolset setup is complete
            if not self.toolset._setup_completed:
                await self.toolset.run_setup()
                self.toolset._setup_completed = True
            # Cache tool descriptions for parameter filtering
            tools_response = await self.toolset.list_tools()
            tools_list = tools_response.get("tools", [])
            for tool in tools_list:
                if isinstance(tool, dict):
                    self._tool_descriptions[tool.get("name", "")] = tool

        except Exception as e:
            logger.error(f"Failed to initialize LocalProvider: {e}")
            # Don't fail initialization, continue anyway
            logger.warning("Continuing without cached tool descriptions")

    async def list_tools(self) -> list[ToolInfo]:
        """List all available tools from the local ToolSet"""
        if self._tools_cache is not None:
            return self._tools_cache

        try:
            import json
            from funcdesc.desc import Description

            from .utils.misc import desc_to_openai_dict

            # Get tools from the local toolset instance
            tools_response = await self.toolset.list_tools()
            tools_list = tools_response.get("tools", [])

            # Convert to ToolInfo objects with pre-generated OpenAI schema
            tool_infos = []
            for tool in tools_list:
                try:
                    # tool is already a dict serialized from Description.to_json()
                    # Reconstruct Description object from the JSON dict
                    tool_json = json.dumps(tool)
                    desc = Description.from_json(tool_json)

                    # Generate OpenAI format schema using desc_to_openai_dict
                    oai_dict = desc_to_openai_dict(
                        desc, skip_params=[], relaxed_schema=True
                    )

                    # Extract the "function" part (without "type": "function")
                    function_schema = oai_dict.get("function", {})

                    tool_info = ToolInfo(
                        name=desc.name,
                        description=desc.doc or "",
                        inputSchema=function_schema,  # Store "function" part directly
                    )
                    tool_infos.append(tool_info)
                except Exception as e:
                    logger.warning(
                        f"Failed to convert local ToolSet tool '{tool.get('name', 'unknown')}': {e}"
                    )
                    # Skip this tool instead of adding a fake ToolInfo

            # Cache results
            self._tools_cache = tool_infos
            logger.debug(
                f"LocalProvider[{self.toolset_name}] listed {len(tool_infos)} tools: {[tool.name for tool in tool_infos]}"
            )

            return tool_infos

        except Exception as e:
            logger.error(f"Failed to list tools from local ToolSet: {e}")
            raise

    async def call_tool(self, name: str, args: dict) -> Any:
        """Call a tool on the local ToolSet instance"""
        try:
            logger.debug(f"Calling local ToolSet tool '{name}'")

            # Parameter filtering (extract parameters the tool expects)
            tool_desc = self._tool_descriptions.get(name, {})
            param_names = [inp.get("name") for inp in tool_desc.get("inputs", [])]

            filtered_params = {
                k: v for k, v in args.items() if k in param_names or k in _SKIP_PARAMS
            }

            # Get the tool method from the toolset
            tool_method = getattr(self.toolset, name, None)
            if tool_method is None:
                raise AttributeError(
                    f"Tool '{name}' not found in ToolSet '{self.toolset_name}'"
                )

            # Call the tool method directly (in-memory)
            # The @tool decorator will handle context_variables injection
            result = await tool_method(**filtered_params)

            # Return raw result directly
            return result

        except Exception as e:
            logger.error(f"Failed to call local ToolSet tool '{name}': {e}")
            raise

    async def shutdown(self):
        """Clean up provider resources"""
        # No remote connections to clean up for local toolset
        logger.info(f"LocalProvider[{self.toolset_name}] shut down")


class ToolSetProvider(ToolProvider):
    """Tool Provider for Pantheon ToolSets"""

    def __init__(
        self,
        toolset_proxy: ToolsetProxy,
    ):
        """Initialize ToolSetProvider"""
        self.toolset_proxy = toolset_proxy
        self._tools_cache: Optional[list[ToolInfo]] = None
        self._tool_descriptions: dict[
            str, dict
        ] = {}  # name -> tool_desc for parameter filtering

    @property
    def toolset_name(self):
        return self.toolset_proxy.toolset_name

    async def initialize(self):
        """Initialize the provider (no-op, lazy loading enabled)."""
        # Tool descriptions are lazily loaded on first list_tools/call_tool call
        # This avoids calling list_tools during Agent creation
        pass

    async def _ensure_tool_descriptions(self):
        """Lazily load tool descriptions for parameter filtering."""
        if self._tool_descriptions:
            return  # Already loaded
        
        try:
            tools_response = await self.toolset_proxy.list_tools()
            tools_list = tools_response.get("tools", [])
            for tool in tools_list:
                if isinstance(tool, dict):
                    self._tool_descriptions[tool.get("name", "")] = tool
        except Exception as e:
            logger.warning(f"Failed to load tool descriptions: {e}")

    async def list_tools(self) -> list[ToolInfo]:
        """List all available tools from the ToolSet"""
        if self._tools_cache is not None:
            return self._tools_cache

        try:
            import json
            from funcdesc.desc import Description
            from .utils.misc import desc_to_openai_dict

            # Get tools from the toolset proxy
            # ToolSet.list_tools() returns: {"success": True, "tools": [...]}
            tools_response = await self.toolset_proxy.list_tools()
            tools_list = tools_response.get("tools", [])

            # Convert to ToolInfo objects with pre-generated OpenAI schema
            tool_infos = []
            for tool in tools_list:
                try:
                    # tool is already a dict serialized from Description.to_json()
                    # Reconstruct Description object from the JSON dict
                    tool_json = json.dumps(tool)
                    desc = Description.from_json(tool_json)

                    # Generate OpenAI format schema using desc_to_openai_dict
                    oai_dict = desc_to_openai_dict(
                        desc, skip_params=[], relaxed_schema=True
                    )

                    # Extract the "function" part (without "type": "function")
                    function_schema = oai_dict.get("function", {})

                    tool_info = ToolInfo(
                        name=desc.name,
                        description=desc.doc or "",
                        inputSchema=function_schema,  # Store "function" part directly
                    )
                    tool_infos.append(tool_info)
                except Exception as e:
                    logger.warning(
                        f"Failed to convert ToolSet tool '{tool.get('name', 'unknown')}': {e}"
                    )
                    # Skip this tool instead of adding a fake ToolInfo

            # Cache results
            self._tools_cache = tool_infos
            logger.debug(
                f"ToolSetProvider{self.toolset_name} listed {len(tool_infos)} tools: {[tool.name for tool in tool_infos]}"
            )

            return tool_infos

        except Exception as e:
            logger.error(f"Failed to list tools from ToolSet: {e}")
            raise

    async def call_tool(self, name: str, args: dict) -> Any:
        """Call a tool on the ToolSet"""
        try:
            logger.debug(f"Calling ToolSet tool '{name}'")

            # Lazy load tool descriptions if not already loaded
            await self._ensure_tool_descriptions()

            # Parameter filtering (extract remote parameters)
            # Only pass parameters that the remote tool expects
            tool_desc = self._tool_descriptions.get(name, {})
            param_names = [inp.get("name") for inp in tool_desc.get("inputs", [])]

            remote_params = {
                k: v for k, v in args.items() if k in param_names or k in _SKIP_PARAMS
            }

            # Call remote tool via proxy and return raw result
            result = await self.toolset_proxy.invoke(name, remote_params)

            # Return raw result directly without any format transformation
            # This ensures remote ToolSet calls have the same return format as local ToolSet calls
            return result

        except Exception as e:
            logger.error(f"Failed to call ToolSet tool '{name}': {e}")
            raise

    async def shutdown(self):
        """Clean up provider resources"""
        # ToolSet proxy cleanup is handled by ChatRoom
        logger.info(f"ToolSetProvider shut down")
