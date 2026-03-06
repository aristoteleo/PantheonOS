"""
Compression plugin for PantheonTeam.

Provides automatic context compression to manage long conversations
and reduce token usage.
"""

from typing import TYPE_CHECKING, Any, Dict

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
        model = active_agent.models[0] if active_agent else self.model
        
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
        # Use ace/learning directory for unified management with ACE learning data
        learning_config = settings.get_learning_config()
        compression_dir = learning_config["learning_dir"]
        
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
