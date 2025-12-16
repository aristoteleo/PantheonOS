"""
Pantheon Settings Module

Unified configuration loading with three-layer priority:
1. ~/.pantheon/ (user global config - highest priority)
2. pwd/.pantheon/ (project config)
3. pantheon/factory/templates/ (package defaults - lowest priority)

Additional overrides:
- Command line arguments (highest)
- Environment variables
- env_file (default: .env)

Supports JSONC format (JSON with Comments) for configuration files.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .utils.model_selector import ModelSelector

from dotenv import load_dotenv

from .utils.log import logger


def strip_jsonc_comments(content: str) -> str:
    """
    Strip JavaScript-style comments from JSONC content.

    Supports:
    - Single-line comments: // ...
    - Multi-line comments: /* ... */

    Args:
        content: JSONC string with comments

    Returns:
        Valid JSON string without comments
    """
    # Remove multi-line comments first
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)

    # Remove single-line comments (but not inside strings)
    # This is a simplified approach - handles most cases
    lines = []
    for line in content.split("\n"):
        # Find // that's not inside a string
        in_string = False
        escape_next = False
        result = []
        i = 0
        while i < len(line):
            char = line[i]
            if escape_next:
                result.append(char)
                escape_next = False
                i += 1
                continue
            if char == "\\":
                result.append(char)
                escape_next = True
                i += 1
                continue
            if char == '"':
                in_string = not in_string
                result.append(char)
                i += 1
                continue
            if not in_string and i + 1 < len(line) and line[i : i + 2] == "//":
                # Found comment start, stop here
                break
            result.append(char)
            i += 1
        lines.append("".join(result))

    return "\n".join(lines)


def load_jsonc(path: Path) -> Dict[str, Any]:
    """
    Load a JSONC file (JSON with Comments).

    Args:
        path: Path to the JSONC file

    Returns:
        Parsed JSON as dictionary, or empty dict if file doesn't exist
    """
    if not path.exists():
        return {}

    try:
        content = path.read_text(encoding="utf-8")
        clean_json = strip_jsonc_comments(content)
        return json.loads(clean_json)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSONC file {path}: {e}")
        return {}
    except Exception as e:
        logger.warning(f"Failed to load JSONC file {path}: {e}")
        return {}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries.

    - Dict values are recursively merged
    - List values are replaced (not merged)
    - Other values are replaced

    Args:
        base: Base dictionary (lower priority)
        override: Override dictionary (higher priority)

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge dicts
            result[key] = deep_merge(result[key], value)
        else:
            # Replace value (including lists)
            result[key] = value

    return result


class Settings:
    """
    Unified settings manager for Pantheon.

    Loads configuration from three layers with priority:
    1. ~/.pantheon/settings.json (user global)
    2. pwd/.pantheon/settings.json (project)
    3. pantheon/factory/templates/settings.json (package defaults)

    Environment variables and env_file can override api_keys.
    """

    SETTINGS_FILE = "settings.json"
    MCP_FILE = "mcp.json"

    def __init__(self, work_dir: Optional[Path] = None):
        """
        Initialize settings manager.

        Args:
            work_dir: Working directory for project-level config.
                      Defaults to PROJECT_ROOT (captured at module load, before any chdir).
        """
        from .constant import PROJECT_ROOT

        self.work_dir = Path(work_dir) if work_dir else PROJECT_ROOT
        self.user_home = Path.home() / ".pantheon"
        self.pantheon_dir = self.work_dir / ".pantheon"
        self.package_templates = Path(__file__).parent / "factory" / "templates"

        self._settings: Dict[str, Any] = {}
        self._mcp: Dict[str, Any] = {}
        self._loaded = False

    @property
    def config_dir(self) -> Path:
        """Alias for pantheon_dir (project config directory)."""
        return self.pantheon_dir

    @property
    def endpoint_workspace(self) -> Path:
        """
        The configured workspace path for the endpoint.
        Uses 'endpoint.workspace_path' from config, resolving relative paths against work_dir.
        Defaults to pantheon_dir.
        """
        self._ensure_loaded()
        path_str = self._settings.get("endpoint", {}).get("workspace_path")

        if path_str:
            path = Path(path_str)
            if path.is_absolute():
                return path
            # Resolve relative workspace path against user's launch directory (work_dir)
            return (self.work_dir / path).resolve()

        return self.work_dir

    @property
    def workspace(self) -> Path:
        """Alias for endpoint_workspace - the main workspace directory."""
        return self.endpoint_workspace

    @property
    def os(self) -> str:
        """Current operating system name (e.g., 'macos', 'linux', 'windows')."""
        import platform

        system = platform.system().lower()
        if system == "darwin":
            return "macos"
        return system

    @property
    def agents_dir(self) -> Path:
        return self.pantheon_dir / "agents"

    @property
    def teams_dir(self) -> Path:
        return self.pantheon_dir / "teams"

    @property
    def prompts_dir(self) -> Path:
        return self.pantheon_dir / "prompts"

    @property
    def memory_dir(self) -> Path:
        return self.pantheon_dir / "memory"

    @property
    def brain_dir(self) -> Path:
        return self.pantheon_dir / "brain"

    @property
    def packages_dir(self) -> Path:
        return self.pantheon_dir / "packages"

    @property
    def skills_dir(self) -> Path:
        return self.pantheon_dir / "skills"

    @property
    def ace_dir(self) -> Path:
        """Directory for ACE long-term memory data."""
        return self.pantheon_dir / "ace"

    def get_model_selector(self) -> "ModelSelector":
        """
        Get a ModelSelector instance for smart model selection.

        The ModelSelector uses environment API keys to detect available providers
        and resolves model tags to fallback chains.

        Returns:
            ModelSelector instance
        """
        from .utils.model_selector import ModelSelector

        return ModelSelector(self)

    def get_ace_config(self) -> Dict[str, Any]:
        """
        Get ACE (Agentic Context Engineering) configuration.

        Returns:
            Dict with ACE config: enable, skillbook_path, learning_model, etc.
        """
        self._ensure_loaded()
        ace = self._settings.get("ace", {})
        
        return {
            "enable": ace.get("enable", False),  # Disabled by default (requires structured output support)
            "skillbook_path": str(
                self.ace_dir / ace.get("skillbook_path", "skillbook.json")
            ),
            "learning_model": ace.get("learning_model"),  # None uses Agent's default
            "learning_dir": str(
                self.ace_dir / ace.get("learning_dir", "learning")
            ),
            "max_skills_per_section": ace.get("max_skills_per_section", 30),
            "max_content_length": ace.get("max_content_length", 500),  # For Skillbook (skill content limit)
            "max_tool_arg_length": ace.get("max_tool_arg_length", 200),  # For learning trajectory
            "max_tool_output_length": ace.get("max_tool_output_length", 200),  # For learning trajectory
            "cleanup_after_learning": ace.get("cleanup_after_learning", False),
            "enable_agent_scope": ace.get("enable_agent_scope", False),
        }

    def get_compression_config(self) -> Dict[str, Any]:
        """
        Get context compression configuration.
        
        Returns:
            Dict with compression config: enable, threshold, preserve_recent_messages, etc.
        """
        self._ensure_loaded()
        compression = self._settings.get("context_compression", {})
        
        return {
            "enable": compression.get("enable", False),  # Disabled by default
            "threshold": compression.get("threshold", 0.8),
            "preserve_recent_messages": compression.get("preserve_recent_messages", 5),
            "max_tool_arg_length": compression.get("max_tool_arg_length", 2000),
            "max_tool_output_length": compression.get("max_tool_output_length", 5000),
            "retry_after_messages": compression.get("retry_after_messages", 10),
        }

    def get_detection_config(self) -> Dict[str, bool]:
        """
        Get attachment detection configuration.

        Returns:
            Dict with detection flags: detect_images, detect_files, detect_links, detect_structured
        """
        self._ensure_loaded()
        detection = self._settings.get("detection", {})

        return {
            "detect_images": detection.get("detect_images", True),
            "detect_files": detection.get("detect_files", False),  # Disabled by default
            "detect_links": detection.get("detect_links", False),  # Disabled by default
            "detect_structured": detection.get("detect_structured", True),
        }

    def get_context_variables(self) -> dict[str, str]:
        """Get context variables for system prompt injection.

        These variables are automatically injected into agent context
        and can be used in system prompts via ${{ variable_name }} syntax.

        Returns:
            Dict with keys: pantheon_dir, workspace, os
        """
        return {
            "pantheon_dir": str(self.pantheon_dir),
            "workspace": str(self.workspace),
            "os": self.os,
        }

    def _ensure_loaded(self) -> None:
        """Lazy load configuration on first access."""
        if not self._loaded:
            self._load()
            self._loaded = True

    def _merge_settings(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """Helper to deep merge source into target."""
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                self._merge_settings(target[key], value)
            else:
                target[key] = value

    def _load(self) -> None:
        """
        Load configuration from all three layers and merge.

        Priority (highest to lowest):
        1. ~/.pantheon/settings.json
        2. pwd/.pantheon/settings.json
        3. pantheon/factory/templates/settings.json
        """
        # 1. Package defaults (Lowest priority)
        defaults = load_jsonc(self.package_templates / self.SETTINGS_FILE)
        self._settings = defaults
        logger.debug(f"Loaded package defaults from {self.package_templates}")

        # 2. Project config (Medium priority) - using pantheon_dir
        project = load_jsonc(self.pantheon_dir / self.SETTINGS_FILE)
        self._merge_settings(self._settings, project)
        if project:
            logger.debug(f"Loaded project config from {self.pantheon_dir}")

        # 3. User config (Highest priority)
        user = load_jsonc(self.user_home / self.SETTINGS_FILE)
        self._merge_settings(self._settings, user)
        if user:
            logger.debug(f"Loaded user config from {self.user_home}")

        # Load env_file
        env_file = self._settings.get("env_file", ".env")
        env_path = self.work_dir / env_file
        if env_path.exists():
            load_dotenv(env_path)
            logger.debug(f"Loaded environment from {env_path}")

        self._load_mcp()

    def _load_mcp(self) -> None:
        """Load MCP configuration."""
        # 1. Package Defaults
        self._mcp = load_jsonc(self.package_templates / self.MCP_FILE)

        # 2. Project Config (Merge)
        mcp_project = load_jsonc(self.pantheon_dir / self.MCP_FILE)
        self._merge_settings(self._mcp, mcp_project)
        if mcp_project:
            logger.debug(f"Loaded project MCP config from {self.pantheon_dir}")

        # 3. User Config (Merge)
        mcp_user = load_jsonc(self.user_home / self.MCP_FILE)
        self._merge_settings(self._mcp, mcp_user)
        if mcp_user:
            logger.debug(f"Loaded user MCP config from {self.user_home}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value using dot notation.

        Args:
            key: Dot-separated path (e.g., 'endpoint.log_level')
            default: Default value if key not found

        Returns:
            Setting value or default
        """
        self._ensure_loaded()

        parts = key.split(".")
        value = self._settings

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default

        return value

    def get_api_key(self, key: str) -> Optional[str]:
        """
        Get an API key with environment variable priority.

        Priority:
        1. Environment variable (os.environ)
        2. env_file loaded values
        3. settings.json api_keys section

        Args:
            key: API key name (e.g., 'OPENAI_API_KEY')

        Returns:
            API key value or None
        """
        self._ensure_loaded()

        # Environment variable has highest priority
        env_value = os.environ.get(key)
        if env_value:
            return env_value

        # Fall back to settings.json
        return self._settings.get("api_keys", {}).get(key)

    def get_endpoint_config(self) -> Dict[str, Any]:
        """
        Get merged endpoint configuration.

        Returns:
            Endpoint config dict compatible with EndpointConfig
        """
        self._ensure_loaded()

        config = {}

        # Endpoint settings
        endpoint = self._settings.get("endpoint", {})
        config.update(
            {
                "service_name": endpoint.get(
                    "service_name", "pantheon-chatroom-endpoint"
                ),
                "workspace_path": endpoint.get(
                    "workspace_path", str(self.pantheon_dir)
                ),
                "log_level": endpoint.get("log_level", "INFO"),
                "allow_file_transfer": endpoint.get("allow_file_transfer", True),
                "local_toolset_timeout": endpoint.get("local_toolset_timeout", 600),
                "local_toolset_execution_mode": endpoint.get(
                    "local_toolset_execution_mode", "direct"
                ),
            }
        )

        # Services settings
        services = self._settings.get("services", {})
        config["builtin_services"] = services.get("builtin", [])
        config["service_modes"] = services.get("modes", {"default": "local"})

        # MCP settings (from mcp.json)
        config["mcp_servers"] = self._mcp.get("servers", {})
        config["auto_start_mcp_servers"] = self._mcp.get("auto_start", [])

        return config

    def get_remote_config(self) -> Dict[str, Any]:
        """
        Get remote backend configuration.

        Returns:
            Remote config dict with backend and server_urls
        """
        self._ensure_loaded()

        remote = self._settings.get("remote", {})

        # Environment variable override for NATS servers
        nats_env = os.environ.get("NATS_SERVERS", "")
        if nats_env:
            server_urls = [s.strip() for s in nats_env.split("|") if s.strip()]
        else:
            server_urls = remote.get("nats_servers", ["nats://localhost:4222"])

        return {
            "backend": os.environ.get("PANTHEON_REMOTE_BACKEND")
            or remote.get("backend", "nats"),
            "server_urls": server_urls,
            "server_host": os.environ.get("PANTHEON_SERVER_HOST")
            or remote.get("server_host", "localhost"),
        }

    def get_knowledge_config(self) -> Dict[str, Any]:
        """
        Get knowledge/RAG configuration.

        Returns:
            Knowledge config dict with qdrant settings
        """
        self._ensure_loaded()

        knowledge = self._settings.get("knowledge", {})
        qdrant = knowledge.get("qdrant", {})

        # Environment variable overrides
        return {
            "storage_path": knowledge.get("storage_path", "~/.pantheon-knowledge"),
            "qdrant": {
                "location": os.environ.get("QDRANT_LOCATION") or qdrant.get("location"),
                "path": os.environ.get("QDRANT_PATH") or qdrant.get("path"),
                "api_key": os.environ.get("QDRANT_API_KEY") or qdrant.get("api_key"),
                "prefer_grpc": (
                    os.environ.get("QDRANT_PREFER_GRPC", "").lower() == "true"
                    if os.environ.get("QDRANT_PREFER_GRPC")
                    else qdrant.get("prefer_grpc", False)
                ),
            },
        }

    def get_mcp_config(self) -> Dict[str, Any]:
        """
        Get MCP server configuration.

        Returns:
            MCP config dict with servers and auto_start
        """
        self._ensure_loaded()
        return self._mcp

    @property
    def settings(self) -> Dict[str, Any]:
        """Get the full settings dictionary."""
        self._ensure_loaded()
        return self._settings

    @property
    def mcp(self) -> Dict[str, Any]:
        """Get the full MCP configuration dictionary."""
        self._ensure_loaded()
        return self._mcp


# Global settings instance (lazy loaded)
_settings: Optional[Settings] = None


def get_settings(work_dir: Optional[Path] = None) -> Settings:
    """
    Get or create the global settings instance.

    Args:
        work_dir: Working directory. If provided, creates new instance.

    Returns:
        Settings instance
    """
    global _settings

    if work_dir is not None:
        # Create new instance with custom work_dir
        return Settings(work_dir)

    if _settings is None:
        _settings = Settings()

    return _settings


def reset_settings() -> None:
    """Reset the global settings instance (for testing)."""
    global _settings
    _settings = None


__all__ = [
    "Settings",
    "get_settings",
    "reset_settings",
    "load_jsonc",
    "strip_jsonc_comments",
    "deep_merge",
]
