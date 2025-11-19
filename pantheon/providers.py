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
    """Tool Provider for Model Context Protocol (MCP) servers"""

    _client_cache: dict[str, Client] = {}
    _cache_lock = Lock()

    def __init__(
        self,
        config: "MCPServerConfig | None" = None,
        model: str = "gpt-4",
        client: Optional[Client] = None,
    ):
        """Initialize MCPProvider"""
        self.config = config
        self.model = model
        self._client: Client = client
        self._tools_cache: Optional[list[ToolInfo]] = None

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
        """Get or create cached HTTP client"""
        if not self.config:
            raise ValueError(
                "MCPProvider was not initialized with config. Use client parameter instead."
            )

        uri = self.config.uri
        if not uri:
            raise ValueError(f"MCP server '{self.config.name}' has no URI configured")

        # Check cache first
        with self._cache_lock:
            if uri in self._client_cache:
                client = self._client_cache[uri]
                # Connection state will be checked when used in async with block
                return client

        # Create new client with sampling handler
        logger.info(f"Creating new MCP client for {uri}")
        client = Client(
            uri,  # First parameter: transport (URI string, FastMCP will infer HTTP transport)
            name="pantheon-agent",  # Second parameter: client name
            sampling_handler=self._sampling_handler,
        )

        logger.info(f"MCP client created for {uri} with sampling support")

        # Cache it (connection will be established on first use within async with)
        with self._cache_lock:
            self._client_cache[uri] = client

        return client

    async def initialize(self):
        """Initialize the provider"""
        try:
            if not self._client:
                # Only get client if not already provided
                self._client = await self._get_client()

            provider_name = self.config.name if self.config else "unknown"
            logger.debug(
                f"MCPProvider '{provider_name}' initialized with sampling support (model: {self.model})"
            )

        except Exception as e:
            provider_name = self.config.name if self.config else "unknown"
            logger.error(f"Failed to initialize MCPProvider '{provider_name}': {e}")
            raise

    async def list_tools(self) -> list[ToolInfo]:
        """List all available tools from the MCP server"""
        if self._tools_cache is not None:
            return self._tools_cache

        if not self._client:
            await self.initialize()

        try:
            # Use async with to establish connection for this operation
            async with self._client:
                # Get tools from MCP server
                tools_response = await self._client.list_tools()

                # Convert to ToolInfo objects with OpenAI function schema
                # Store complete tool dict in inputSchema for direct use in Agent
                tool_infos = []
                for tool in tools_response:
                    # MCP's inputSchema is already in JSON Schema format
                    params = tool.inputSchema if hasattr(tool, "inputSchema") else {}

                    # Build complete OpenAI function schema
                    # This is the "function" part of the tool, ready to be wrapped
                    function_schema = {
                        "name": tool.name,
                        "description": tool.description,
                        "strict": False,
                        "parameters": params,
                    }

                    tool_info = ToolInfo(
                        name=tool.name,
                        description=tool.description,
                        inputSchema=function_schema,  # Store just the "function" part
                    )
                    tool_infos.append(tool_info)

                # Cache results
                self._tools_cache = tool_infos
                provider_name = self.config.name if self.config else "unknown"
                logger.debug(
                    f"MCPProvider '{provider_name}' listed {len(tool_infos)} tools"
                )

                return tool_infos

        except Exception as e:
            provider_name = self.config.name if self.config else "unknown"
            logger.error(f"Failed to list tools from MCP server '{provider_name}': {e}")
            raise

    async def call_tool(self, name: str, args: dict) -> Any:
        """Call a tool on the MCP server"""
        if not self._client:
            await self.initialize()

        provider_name = self.config.name if self.config else "unknown"

        try:
            logger.debug(f"Calling MCP tool '{name}' on '{provider_name}'")

            # Use async with to establish connection for this operation
            async with self._client:
                result = await self._client.call_tool(name, args)

                # Priority 1: .structured_content (raw MCP JSON)
                # Universal, portable format compatible with all systems
                if (
                    hasattr(result, "structured_content")
                    and result.structured_content is not None
                ):
                    logger.debug(
                        f"MCPProvider '{provider_name}': Extracted result via .structured_content (universal JSON format)"
                    )
                    return result.structured_content

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
                                    f"MCPProvider '{provider_name}': Extracted JSON from content text"
                                )
                                return parsed
                            except (json.JSONDecodeError, TypeError):
                                # Not JSON, return text as-is
                                logger.debug(
                                    f"MCPProvider '{provider_name}': Extracted plain text from content"
                                )
                                return text

                # Priority 3: .data (Python objects - fallback when JSON unavailable)
                # Note: .data may return Python objects (dataclass, NamedTuple) which are
                # not universally portable. Used as fallback when structured_content is unavailable.
                if hasattr(result, "data") and result.data is not None:
                    logger.debug(
                        f"MCPProvider '{provider_name}': Extracted result via .data "
                        f"(type: {type(result.data).__name__}) - Note: Python objects may not be universally portable"
                    )
                    return result.data

                # Fallback: Return entire result
                logger.warning(
                    f"MCPProvider '{provider_name}': Could not extract result from CallToolResult "
                    f"for tool '{name}'. Result structure: "
                    f"structured_content={result.structured_content}, "
                    f"content_blocks={len(result.content) if result.content else 0}, "
                    f"data={result.data}"
                )
                return result

        except Exception as e:
            logger.error(f"Failed to call MCP tool '{name}': {e}")
            raise

    async def shutdown(self):
        """Clean up provider resources"""
        if self._client:
            try:
                # Clear the tool cache to ensure fresh listings on next use
                self._tools_cache = None
                provider_name = self.config.name if self.config else "unknown"
                logger.info(f"MCPProvider '{provider_name}' shut down")
            except Exception as e:
                provider_name = self.config.name if self.config else "unknown"
                logger.error(f"Error shutting down MCPProvider '{provider_name}': {e}")


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
                        desc, skip_params=[], litellm_mode=True
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
        """Initialize the provider"""
        try:
            # Cache tool descriptions for parameter filtering
            tools_response = await self.toolset_proxy.list_tools()

            # ToolsetProxy always returns dict format
            tools_list = tools_response.get("tools", [])
            for tool in tools_list:
                if isinstance(tool, dict):
                    self._tool_descriptions[tool.get("name", "")] = tool

        except Exception as e:
            logger.error(f"Failed to initialize ToolSetProvider: {e}")
            # Don't fail initialization, continue anyway
            logger.warning("Continuing without cached tool descriptions")

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
                        desc, skip_params=[], litellm_mode=True
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
