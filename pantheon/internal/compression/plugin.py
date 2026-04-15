"""
Compression plugin for PantheonTeam.

Provides automatic context compression to manage long conversations
and reduce token usage.
"""

from typing import TYPE_CHECKING, Any, Dict
from datetime import datetime

from pantheon.team.plugin import TeamPlugin
from pantheon.utils.log import logger

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam
    from pantheon.internal.memory import Memory
    from pantheon.internal.compression import ContextCompressor


class CompressionPlugin(TeamPlugin):
    """
    Plugin that adds context compression capabilities to PantheonTeam.
    
    Automatically compresses conversation history when token usage
    exceeds configured thresholds, reducing memory footprint while
    preserving important context.
    
    Configuration:
        enable: Enable compression (default: False)
        threshold: Token usage threshold to trigger compression (default: 0.8)
        preserve_recent_messages: Number of recent messages to keep uncompressed (default: 5)
        max_tool_arg_length: Max length for tool arguments (default: 2000)
        max_tool_output_length: Max length for tool outputs (default: 5000)
        retry_after_messages: Minimum messages before retrying compression (default: 10)
    
    Example:
        plugin = CompressionPlugin(config={
            "enable": True,
            "threshold": 0.8,
            "preserve_recent_messages": 5,
        })
        team = PantheonTeam(agents=agents, plugins=[plugin])
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize compression plugin.
        
        Args:
            config: Compression configuration dict (from settings.get_compression_config())
                   Should include 'compression_model' field
        """
        self.config = config
        self.model = config.get("compression_model", "normal")  # Get model from config
        self.compressor: "ContextCompressor | None" = None
        self._enabled = config.get("enable", False)
    
    async def on_team_created(self, team: "PantheonTeam") -> None:
        """
        Initialize compression resources.
        
        Always creates ContextCompressor. The 'enable' flag only controls
        whether auto-compression happens in on_run_start.
        """
        from pantheon.internal.compression import CompressionConfig, ContextCompressor
        
        # Create compression config
        compression_config = CompressionConfig(
            enable=self._enabled,  # Controls auto-compression
            threshold=self.config.get("threshold", 0.8),
            preserve_recent_messages=self.config.get("preserve_recent_messages", 5),
            max_tool_arg_length=self.config.get("max_tool_arg_length", 2000),
            max_tool_output_length=self.config.get("max_tool_output_length", 5000),
            retry_after_messages=self.config.get("retry_after_messages", 10),
        )
        
        # Use configured model (do NOT override with team's model)
        # Compression has its own model configuration
        
        # Always create compressor (for force_compress support)
        self.compressor = ContextCompressor(compression_config, self.model)
        
        if self._enabled:
            logger.info(f"Compression plugin initialized with auto-compression (model={self.model}, threshold={compression_config.threshold})")
        else:
            logger.info(f"Compression plugin initialized (model={self.model}, auto-compression disabled, manual compression available)")
    
    async def on_run_start(self, team: "PantheonTeam", user_input: str, context: dict) -> None:
        """
        Check and perform compression before run starts.
        
        Only performs auto-compression if 'enable' flag is True.
        
        Args:
            team: The PantheonTeam instance
            user_input: User's input message
            context: Run context containing memory
        """
        # Only auto-compress if enabled
        if not self._enabled or not self.compressor:
            return
        
        memory = context.get("memory")
        if not memory:
            return
        
        # Get active agent's model
        active_agent = team.get_active_agent(memory)
        model = active_agent.models[0] if active_agent and getattr(active_agent, "models", None) else self.model
        
        # Check if compression is needed
        if self.compressor.should_compress(memory._messages, model):
            await self._perform_compression(team, memory)
    
    async def _perform_compression(self, team: "PantheonTeam", memory: "Memory", force: bool = False) -> dict:
        """
        Perform context compression on the memory.
        
        Args:
            team: The PantheonTeam instance
            memory: Memory instance to compress
            force: If True, bypass chunk size checks and force compression
            
        Returns:
            dict with compression result info
        """
        from pantheon.settings import get_settings

        settings = get_settings()
        compression_dir = str(settings.learning_dir / "pipeline")

        # Pre-compression flush: call pre_compression hook on all plugins
        session_id = getattr(memory, "id", "default")
        # Find memory plugin first (outside try block so it's always available)
        memory_plugin = None
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        for plugin in team.plugins:
            if isinstance(plugin, MemorySystemPlugin):
                memory_plugin = plugin
                break

        try:
            from pantheon.utils.misc import run_func
            for plugin in team.plugins:
                if plugin is self:
                    continue
                result = await run_func(plugin.pre_compression, team, session_id, memory._messages)
                if result:
                    logger.info(f"Pre-compression flush from {plugin.__class__.__name__}")
        except Exception as e:
            logger.warning(f"Pre-compression hook failed: {e}")

        # Session Note Compact shortcut: zero LLM call compression
        if memory_plugin and not force:
            sm_result = await self._try_session_note_compact(memory_plugin, memory)
            if sm_result is not None:
                return sm_result

        result = await self.compressor.compress(
            messages=memory._messages,
            compression_dir=compression_dir,
            force=force,
        )
        
        if result.compression_message:
            # Get compression range to know which messages to replace
            compress_start, compress_end = self.compressor._get_compression_range(
                memory._messages
            )
            
            # Non-destructive compression: Insert compression message AFTER the compressed block
            new_messages = (
                memory._messages[:compress_end]
                + [result.compression_message]
                + memory._messages[compress_end:]
            )
            memory._messages = new_messages
            # Compression inserts a message in the middle of the history.
            # append_messages() assumes new messages are at the end and would miss it.
            # Force a full rewrite to ensure the compression checkpoint is persisted.
            if memory._backend:
                memory._backend.rewrite_messages(memory.id, memory._messages)
                memory._backend._last_persisted_count[memory.id] = len(memory._messages)

            logger.info(
                f"Context compression checkpoint inserted at index {compress_end}. "
                f"Compressed {compress_end - compress_start} messages ({result.original_tokens} -> {result.new_tokens} tokens)."
            )
            
            return {
                "success": True,
                "compressed_messages": compress_end - compress_start,
                "original_tokens": result.original_tokens,
                "new_tokens": result.new_tokens,
            }
        
        # Handle different failure statuses
        from pantheon.internal.compression.compressor import CompressionStatus
        
        if result.status == CompressionStatus.SKIPPED:
            return {"success": False, "message": "Not enough messages to compress"}
        elif result.status == CompressionStatus.FAILED_INFLATED:
            return {"success": False, "message": "Compression would increase token count"}
        elif result.status == CompressionStatus.FAILED_ERROR:
            error_msg = result.error or "Unknown compression error"
            return {"success": False, "message": f"Compression failed: {error_msg}"}
        
        return {"success": False, "message": "Compression did not produce a result"}
    
    async def force_compress(self, team: "PantheonTeam", memory: "Memory") -> dict:
        """
        Force context compression regardless of threshold.

        Args:
            team: The PantheonTeam instance
            memory: Memory instance to compress

        Returns:
            dict with compression result info
        """
        if not self.compressor:
            return {"success": False, "message": "Compression not enabled in settings"}

        # Perform compression with force=True to bypass chunk size checks
        return await self._perform_compression(team, memory, force=True)

    async def _try_session_note_compact(
        self, memory_plugin: "MemorySystemPlugin", memory: "Memory"
    ) -> dict | None:
        """Session Note Compact: zero LLM call compression using session notes.

        Returns compression result dict, or None to fall through to full compact.
        """
        session_id = getattr(memory, "id", "default")

        try:
            # Wait for any in-flight session note extraction
            await memory_plugin.runtime.wait_for_session_note(session_id)

            # Check if session note has real content
            if memory_plugin.runtime.is_session_note_empty(session_id):
                return None

            content = memory_plugin.runtime.get_session_note_for_compact(session_id)
            if not content:
                return None

            # Get the boundary: how far session note covers
            boundary = memory_plugin.runtime.get_session_note_boundary(
                session_id, memory._messages
            )
            if boundary is None or boundary <= 0:
                return None

            # Calculate messages to keep (preserve recent + tool pairs)
            keep_start = max(boundary, len(memory._messages) - 5)
            # Adjust to not split tool_use/tool_result pairs
            keep_start = self._adjust_for_tool_pairs(memory._messages, keep_start)

            if keep_start >= len(memory._messages):
                return None

            # Build compact result — use role="compression" to match LLM compression format
            # Non-destructive: insert checkpoint at keep_start, preserving all messages
            original_count = len(memory._messages)
            compression_index = sum(
                1 for m in memory._messages if m.get("role") == "compression"
            ) + 1
            summary_msg = {
                "role": "compression",
                "content": (
                    f"{{{{ CHECKPOINT {compression_index} }}}}\n"
                    f"**The earlier parts of this conversation have been truncated due to its long length. "
                    f"The following content summarizes the truncated context so that you may continue your work.**\n\n"
                    f"{content}"
                ),
                "_metadata": {
                    "method": "session_note_compact",
                    "original_message_count": original_count,
                    "compressed_token_count": len(memory._messages),
                    "compression_index": compression_index,
                    "timestamp": datetime.now().isoformat(),
                    "current_cost": 0.0,
                },
            }
            # Insert checkpoint at keep_start (non-destructive, like LLM compression)
            memory._messages = (
                memory._messages[:keep_start]
                + [summary_msg]
                + memory._messages[keep_start:]
            )
            # Compression inserts in the middle — must rewrite to persist the checkpoint.
            if memory._backend:
                memory._backend.rewrite_messages(memory.id, memory._messages)
                memory._backend._last_persisted_count[memory.id] = len(memory._messages)

            logger.info(
                f"Session Note Compact: inserted checkpoint at index {keep_start} "
                f"(zero LLM calls)"
            )
            return {
                "success": True,
                "method": "session_note_compact",
                "compressed_messages": original_count - len(memory._messages),
            }
        except Exception as e:
            logger.debug(f"Session Note Compact failed, falling back: {e}")
            return None

    @staticmethod
    def _adjust_for_tool_pairs(messages: list[dict], start: int) -> int:
        """Adjust start index to not split tool_use/tool_result pairs."""
        if start <= 0 or start >= len(messages):
            return start
        # If we'd start at a tool result, include the preceding tool use
        msg = messages[start]
        if msg.get("role") == "tool":
            return max(0, start - 1)
        return start


def _create_compression_plugin(config: dict, settings) -> CompressionPlugin:
    """Factory function for plugin registry."""
    return CompressionPlugin(config)


# Register with plugin registry
from pantheon.team.plugin_registry import PluginDef, register_plugin

register_plugin(PluginDef(
    name="compression",
    config_key="context_compression",
    enabled_key="enable",
    factory=_create_compression_plugin,
    priority=200,
))
