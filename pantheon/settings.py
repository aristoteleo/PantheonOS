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

    def __init__(self, work_dir: Optional[Path] = None, env_override: bool = False):
        """
        Initialize settings manager.

        Args:
            work_dir: Working directory for project-level config.
                      Defaults to PROJECT_ROOT (captured at module load, before any chdir).
            env_override: Whether to override existing environment variables when loading .env file.
                         Default: False (respects dynamically set environment variables).
                         Set to True to force .env values to override existing environment variables.

        Note:
            API keys should be set via:
            - Environment variables: export OPENAI_API_KEY="sk-..."
            - .env file: OPENAI_API_KEY=sk-...
            - settings.json api_keys section

            Use LLM Proxy mode for secure API key handling (set LLM_API_BASE environment variable).
        """
        from .constant import PROJECT_ROOT

        self.work_dir = Path(work_dir) if work_dir else PROJECT_ROOT
        self.user_home = Path.home() / ".pantheon"
        self.pantheon_dir = self.work_dir / ".pantheon"
        self.package_templates = Path(__file__).parent / "factory" / "templates"

        self._settings: Dict[str, Any] = {}
        self._mcp: Dict[str, Any] = {}
        self._loaded = False
        self._env_override = env_override  # Control .env loading behavior

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
    def learning_dir(self) -> Path:
        """Directory for learning long-term memory data."""
        return self.pantheon_dir / "learning"

    @property
    def logs_dir(self) -> Path:
        """Directory for log files (REPL logs, etc.)."""
        return self.pantheon_dir / "logs"

    @property
    def tmp_dir(self) -> Path:
        """Directory for temporary files (large tool outputs, etc.)."""
        return self.pantheon_dir / "tmp"

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

    def get_section(self, key: str, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Get a raw config section by key.

        Generic interface for plugins to read their own config without
        requiring a dedicated method on Settings.

        Example:
            raw = settings.get_section("memory_system")
            raw = settings.get_section("my_plugin", {"enabled": False})
        """
        self._ensure_loaded()
        return dict(self._settings.get(key, default or {}))

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
            "compression_model": compression.get("compression_model"),  # None uses Agent's default
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
        1. pwd/.pantheon/settings.json  (project-specific)
        2. ~/.pantheon/settings.json    (user defaults)
        3. pantheon/factory/templates/settings.json  (package defaults)
        """
        # 1. Package defaults (Lowest priority)
        defaults = load_jsonc(self.package_templates / self.SETTINGS_FILE)
        self._settings = defaults
        logger.debug(f"Loaded package defaults from {self.package_templates}")

        # 2. User config (Medium priority - personal defaults)
        user = load_jsonc(self.user_home / self.SETTINGS_FILE)
        self._merge_settings(self._settings, user)
        if user:
            logger.debug(f"Loaded user config from {self.user_home}")

        # 3. Project config (Highest priority - project-specific overrides)
        project = load_jsonc(self.pantheon_dir / self.SETTINGS_FILE)
        self._merge_settings(self._settings, project)
        if project:
            logger.debug(f"Loaded project config from {self.pantheon_dir}")

        # Load env_file
        # Priority: dynamically set variables > .env file > defaults
        # Use _env_override to control whether .env overwrites existing environment variables
        env_file = self._settings.get("env_file", ".env")
        env_path = self.work_dir / env_file
        if env_path.exists():
            load_dotenv(env_path, override=self._env_override)
            if self._env_override:
                logger.debug(f"Loaded environment from {env_path} (override=True)")
            else:
                logger.debug(f"Loaded environment from {env_path} (respecting existing variables)")

        self._load_mcp()

    def _load_mcp(self) -> None:
        """Load MCP configuration."""
        # 1. Package Defaults (Lowest priority)
        self._mcp = load_jsonc(self.package_templates / self.MCP_FILE)

        # 2. User Config (Medium priority - personal defaults)
        mcp_user = load_jsonc(self.user_home / self.MCP_FILE)
        self._merge_settings(self._mcp, mcp_user)
        if mcp_user:
            logger.debug(f"Loaded user MCP config from {self.user_home}")

        # 3. Project Config (Highest priority - project-specific overrides)
        mcp_project = load_jsonc(self.pantheon_dir / self.MCP_FILE)
        self._merge_settings(self._mcp, mcp_project)
        if mcp_project:
            logger.debug(f"Loaded project MCP config from {self.pantheon_dir}")

    def __getitem__(self, key: str) -> Any:
        """
        Get a setting value using dictionary-style access.

        Args:
            key: Key to look up in settings (e.g., 'endpoint', or any top-level key)

        Returns:
            Setting value

        Raises:
            KeyError: If key not found
        """
        self._ensure_loaded()
        return self._settings[key]

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
        2. settings.json api_keys section

        Args:
            key: API key name (e.g., 'OPENAI_API_KEY')

        Returns:
            API key value or None
        """
        self._ensure_loaded()

        logger.debug(f"[SETTINGS.GET_API_KEY] Looking up key={key}")

        # 1. Environment variable (highest priority)
        env_value = os.environ.get(key)
        if env_value:
            logger.debug(
                f"[SETTINGS.GET_API_KEY] ✓ Retrieved key {key} from "
                f"os.environ | ValueLength={len(env_value)}"
            )
            return env_value

        # 2. Fall back to settings.json
        settings_value = self._settings.get("api_keys", {}).get(key)
        if settings_value:
            logger.debug(
                f"[SETTINGS.GET_API_KEY] ✓ Retrieved key {key} from "
                f"settings.json | ValueLength={len(settings_value)}"
            )
            return settings_value

        logger.debug(f"[SETTINGS.GET_API_KEY] Key {key} not found in any source")
        return None

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
            "jwt": os.environ.get("NATS_JWT"),
            "subject_prefix": os.environ.get("NATS_SUBJECT_PREFIX"),
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
    def enable_mcp_tools(self) -> bool:
        """
        Whether to enable MCP tools injection into agents.
        Defaults to True.
        """
        self._ensure_loaded()
        return self._settings.get("enable_mcp_tools", True)

    @property
    def default_template_auto_update(self) -> bool:
        """Whether to overwrite factory templates (agents/prompts/teams) on startup. Defaults to True."""
        self._ensure_loaded()
        return self._settings.get("default_template_auto_update", True)

    @property
    def tool_timeout(self) -> int:
        """
        Get local toolset timeout configuration.
        Unified timeout for Agents, ToolSets, Jupyter Kernels, and Remote Workers.
        Defaults to 3600s (1 hour) if not configured.
        """
        self._ensure_loaded()
        return self._settings.get("endpoint", {}).get("local_toolset_timeout", 3600)

    @property
    def max_tool_content_length(self) -> int:
        """
        Maximum characters for tool output content.
        Used as fallback for smart truncation at agent level.
        Per-tool thresholds (from token_optimization.py) take priority
        when available.
        Defaults to 50000 (~12.5K tokens).
        """
        self._ensure_loaded()
        return self._settings.get("endpoint", {}).get("max_tool_content_length", 50000)

    @property
    def max_file_read_lines(self) -> int:
        """
        Maximum lines for file read operations.
        Defaults to 800 lines.
        """
        self._ensure_loaded()
        return self._settings.get("endpoint", {}).get("max_file_read_lines", 800)

    @property
    def max_file_read_chars(self) -> int:
        """
        Maximum characters for read_file output (safety valve).

        Acts as an upper bound to prevent unbounded output from pathological
        files. Per-tool thresholds (from token_optimization.py) handle the
        actual LLM-context sizing at Layer 2.

        Defaults to 500000 characters.
        """
        self._ensure_loaded()
        return self._settings.get("endpoint", {}).get("max_file_read_chars", 500000)

    @property
    def max_glob_results(self) -> int:
        """
        Maximum results for glob/search operations.
        Defaults to 100 results.
        """
        self._ensure_loaded()
        return self._settings.get("endpoint", {}).get("max_glob_results", 100)

    @property
    def enable_notebook_execution_logging(self) -> bool:
        self._ensure_loaded()
        return self._settings.get("endpoint", {}).get(
            "enable_notebook_execution_logging", True
        )


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

    def reload(self, env_override: Optional[bool] = None) -> None:
        """Reload settings and .env file.

        This reloads:
        1. Settings files (settings.json) - all three layers
        2. Environment file (.env)
        3. MCP configuration (mcp.json)
        4. Model selector cache (ensures configuration changes take effect)
        5. Package manager cache (ensures packages path is synchronized)

        Args:
            env_override: Whether to override existing environment variables when reloading .env.

                         Default (None): Uses True for reload (user explicitly requests reload)
                         This means reload() will force .env values to override existing variables.

                         Rationale: When a user explicitly calls reload(), they typically want
                         to refresh all settings from files, including .env. This makes .env
                         values "active" again, overriding any runtime modifications.

                         If False: Respects dynamically set environment variables (advanced use)
                         If True: Forces .env values to override everything

        Examples:
            # Standard reload: force .env values
            settings.reload()  # env_override defaults to True

            # Reload but preserve dynamic variables
            settings.reload(env_override=False)

        After reload, any changes in .env or settings.json will take effect immediately.
        """
        # Default to True for reload: user explicitly wants to reload from .env
        if env_override is None:
            env_override = True

        self._env_override = env_override
        self._loaded = False
        self._ensure_loaded()

        # Reset ModelSelector cache to ensure configuration changes take effect.
        # This is critical because ModelSelector caches available providers and
        # detected provider, which depend on environment variables from .env.
        # Without this reset, changes to .env or settings.json won't be reflected
        # in downstream code (e.g., generate_image model selection).
        try:
            from pantheon.utils.model_selector import reset_model_selector

            reset_model_selector()
        except ImportError:
            pass  # model_selector module might not be available

        # Reset PackageManager cache to ensure it uses updated configuration.
        try:
            from pantheon.internal.package_runtime import reset_package_manager

            reset_package_manager()
        except ImportError:
            pass  # package_runtime module might not be available

        logger.info("Settings reloaded successfully")





# Global settings instance (lazy loaded)
_settings: Optional[Settings] = None


def get_settings(
    work_dir: Optional[Path] = None,
    env_override: Optional[bool] = None,
    mode: str = "safe"
) -> Settings:
    """
    Get or create the global settings instance.

    Args:
        work_dir: Working directory. If provided, creates new instance.

        env_override: Explicit override for environment variable behavior.
                     If provided, takes precedence over mode parameter.
                     - True: Force .env values to override existing environment variables
                     - False: Respect dynamically set environment variables

        mode: High-level mode for environment variable handling (if env_override not provided):
              - "safe" (default): env_override=False
                Respects dynamically set environment variables (e.g., from --auto-start-nats).
                Use for initial configuration loading.

              - "reload": env_override=True
                Forces .env file to override existing environment variables.
                Use when user explicitly requests to reload settings (e.g., from frontend).

              - "strict": env_override=True
                Same as "reload" - forces .env values to take precedence.

    Returns:
        Settings instance

    Examples:
        # Default: safe mode, doesn't override dynamic variables
        settings = get_settings()

        # Reload mode: force .env to override everything
        settings = get_settings(mode='reload')

        # Explicit override (takes precedence over mode)
        settings = get_settings(env_override=True)

    Note:
        API keys should be set via environment variables, .env file, or
        settings.json api_keys section.

        For secure API key handling, use LLM Proxy mode by setting
        LLM_API_BASE environment variable.
    """
    global _settings

    # Determine env_override based on mode if not explicitly provided
    if env_override is None:
        if mode in ("reload", "strict"):
            env_override = True
        elif mode == "safe":
            env_override = False
        else:
            raise ValueError(f"Unknown mode: {mode}. Must be 'safe', 'reload', or 'strict'")

    if work_dir is not None:
        # Create new instance with custom parameters
        return Settings(work_dir, env_override=env_override)

    if _settings is None:
        _settings = Settings(env_override=env_override)

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
