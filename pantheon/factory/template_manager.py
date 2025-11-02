"""
Template Manager for Chatroom Templates
Handles loading, validation, and management of chatroom templates
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from ..utils.log import logger

# Built-in services added to all agents when chat_id is provided
BUILTIN_TOOLSETS = ["todolist"]
BUILTIN_MCP_SERVERS = ["context7"]

# Default agents configuration path
DEFAULT_AGENTS_PATH = os.path.join(os.path.dirname(__file__), "agents.yaml")


class AgentsManager:
    """Manages the agents library (agents.yaml)"""

    def __init__(self, agents_path: Optional[str] = None):
        """Initialize agents manager"""
        if agents_path is None:
            # Default location: pantheon/factory/agents.yaml
            agents_path = DEFAULT_AGENTS_PATH

        self.agents_path = Path(agents_path)
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._load_agents()

    def _load_agents(self) -> None:
        """Load agents from YAML file

        Parses the agents.yaml file and caches agent definitions.
        Gracefully handles missing files and parsing errors.
        """
        try:
            if not self.agents_path.exists():
                logger.warning(
                    f"Agents library not found at {self.agents_path}. "
                    f"Create {self.agents_path} to define sub-agents."
                )
                self._agents = {}
                return

            with open(self.agents_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                logger.warning(f"Agents file is empty: {self.agents_path}")
                self._agents = {}
                return

            if "agents" not in data:
                logger.warning(
                    f"Agents file format invalid (missing 'agents' key): {self.agents_path}"
                )
                self._agents = {}
                return

            # Parse agents
            agents_data = data.get("agents", {})
            self._agents = {}

            for agent_name, agent_config in agents_data.items():
                try:
                    # Validate required fields
                    errors = self.validate_agent_config(agent_config)
                    if errors:
                        logger.error(
                            f"Agent '{agent_name}' has validation errors: {errors}"
                        )
                        continue

                    self._agents[agent_name] = agent_config
                    logger.debug(f"Loaded agent: {agent_name}")

                except Exception as e:
                    logger.error(f"Failed to parse agent '{agent_name}': {e}")

            logger.info(f"Loaded {len(self._agents)} agents from {self.agents_path}")

        except Exception as e:
            logger.error(f"Failed to load agents library: {e}")
            self._agents = {}

    def get_agent_config(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get agent configuration by name"""
        if agent_name not in self._agents:
            logger.warning(f"Agent '{agent_name}' not found in agents library")
            return None

        return self._agents.get(agent_name)

    def list_agents(self) -> List[str]:
        """Get list of all available agent names"""
        return sorted(list(self._agents.keys()))

    def validate_agent_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate agent configuration"""
        errors = []

        if not isinstance(config, dict):
            errors.append("Agent configuration must be a dictionary")
            return errors

        # Check required fields
        if not config.get("name"):
            errors.append("Agent 'name' is required and cannot be empty")

        if not config.get("instructions"):
            errors.append("Agent 'instructions' is required and cannot be empty")

        if not config.get("model"):
            errors.append("Agent 'model' is required and cannot be empty")

        # Validate optional fields if present
        if "icon" in config and not isinstance(config["icon"], str):
            errors.append("Agent 'icon' must be a string if provided")

        if "toolsets" in config:
            if not isinstance(config["toolsets"], list):
                errors.append("Agent 'toolsets' must be a list if provided")
            elif not all(isinstance(t, str) for t in config["toolsets"]):
                errors.append("Agent 'toolsets' must contain only strings")

        if "mcp_servers" in config:
            if not isinstance(config["mcp_servers"], list):
                errors.append("Agent 'mcp_servers' must be a list if provided")
            elif not all(isinstance(s, str) for s in config["mcp_servers"]):
                errors.append("Agent 'mcp_servers' must contain only strings")

        return errors

    def get_required_toolsets(self, agent_names: List[str]) -> List[str]:
        """Get all toolsets required by a list of agents"""
        toolsets = set()
        for agent_name in agent_names:
            config = self.get_agent_config(agent_name)
            if config:
                toolsets.update(config.get("toolsets", []))
        return sorted(list(toolsets))

    def get_required_mcp_servers(self, agent_names: List[str]) -> List[str]:
        """Get all MCP servers required by a list of agents"""
        servers = set()
        for agent_name in agent_names:
            config = self.get_agent_config(agent_name)
            if config:
                servers.update(config.get("mcp_servers", []))
        return sorted(list(servers))

    def to_dict(self) -> Dict[str, Any]:
        """Export agents library as dictionary"""
        return {"agents": self._agents}

    def reload(self) -> None:
        """Reload agents from file"""
        logger.info(f"Reloading agents from {self.agents_path}")
        self._load_agents()


# Global agents manager instance
_agents_manager: Optional[AgentsManager] = None


def get_agents_manager(agents_path: Optional[str] = None) -> AgentsManager:
    """Get or create the global agents manager instance"""
    global _agents_manager
    if agents_path is not None:
        # Create new instance with custom path
        return AgentsManager(agents_path)
    if _agents_manager is None:
        _agents_manager = AgentsManager()
    return _agents_manager


@dataclass
class ChatroomTemplate:
    """Represents a chatroom template with unified configuration format.

    The template supports two types of agents:
    - Inline agents: Defined directly in agents_config (at least one required)
    - Sub-agents: Loaded from agents.yaml library via sub_agents specification (optional)
    """

    id: str
    name: str
    description: str
    icon: str
    category: str
    version: str
    agents_config: Dict[str, Any]  # Required: inline agent definitions
    sub_agents: Optional[str | List[str]] = None  # Optional: library agents reference
    tags: List[str] = field(default_factory=list)

    @property
    def required_toolsets(self) -> List[str]:
        """Dynamically compute all toolsets required by agents in this template."""
        toolsets = set()

        for agent_config in self.agents_config.values():
            if isinstance(agent_config, dict) and "toolsets" in agent_config:
                toolsets.update(agent_config["toolsets"])

        return sorted(list(toolsets))

    @property
    def required_mcp_servers(self) -> List[str]:
        """Dynamically compute all MCP servers required by agents in this template."""
        mcp_servers = set()

        for agent_config in self.agents_config.values():
            if isinstance(agent_config, dict) and "mcp_servers" in agent_config:
                mcp_servers.update(agent_config["mcp_servers"])

        return sorted(list(mcp_servers))

    def to_dict(self) -> Dict[str, Any]:
        """Convert template to dictionary format.

        Returns unified format dictionary representation.
        """
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "version": self.version,
            "agents_config": self.agents_config,
            "required_toolsets": self.required_toolsets,
            "required_mcp_servers": self.required_mcp_servers,
            "tags": self.tags,
        }

        if self.sub_agents:
            result["sub_agents"] = self.sub_agents

        return result


class TemplateManager:
    """Manages chatroom templates including loading, validation, and providing access"""

    def __init__(self, chatrooms_path: Optional[str] = None):
        """
        Initialize template manager

        Args:
            chatrooms_path: Path to chatrooms.yaml. If None, uses default (pantheon/factory/chatrooms.yaml).
        """
        if chatrooms_path is None:
            chatrooms_path = os.path.join(os.path.dirname(__file__), "chatrooms.yaml")

        self.chatrooms_path = Path(chatrooms_path)

        # Initialize agents manager (now defined in this module)
        self.agents_manager = get_agents_manager()

        self._templates: Dict[str, ChatroomTemplate] = {}
        self._load_chatrooms()

    def _parse_template(
        self, template_id: str, data: Dict[str, Any]
    ) -> ChatroomTemplate:
        """Parse template data into ChatroomTemplate object

        Unified configuration format:
        - agents_config: Required, contains inline agent definitions (at least one agent)
        - sub_agents: Optional, for loading agents from agents.yaml library

        Args:
            template_id: Template identifier
            data: Template data dictionary

        Returns:
            ChatroomTemplate instance
        """
        # Unified format: use agents_config as-is
        agents_config = data.get("agents_config", {})

        return ChatroomTemplate(
            id=data.get("id", template_id),
            name=data.get("name", template_id),
            description=data.get("description", ""),
            icon=data.get("icon", "🏠"),
            category=data.get("category", "general"),
            version=data.get("version", "1.0.0"),
            agents_config=agents_config,
            sub_agents=data.get("sub_agents"),
            tags=data.get("tags", []),
        )

    def get_template(
        self, template_id: str, default=None
    ) -> Optional[ChatroomTemplate]:
        """Get template by ID"""
        return self._templates.get(template_id, default)

    def list_templates(self) -> List[ChatroomTemplate]:
        """Get list of all available templates"""
        return list(self._templates.values())

    def get_templates_by_category(self, category: str) -> List[ChatroomTemplate]:
        """Get templates filtered by category"""
        return [t for t in self._templates.values() if t.category == category]

    def search_templates(self, query: str) -> List[ChatroomTemplate]:
        """Search templates by name, description, or tags"""
        query_lower = query.lower()
        results = []

        for template in self._templates.values():
            if (
                query_lower in template.name.lower()
                or query_lower in template.description.lower()
                or any(query_lower in tag.lower() for tag in template.tags)
            ):
                results.append(template)

        return results

    def validate_template(self, template: ChatroomTemplate) -> List[str]:
        """Validate template configuration in unified format.

        Validates:
        - Required template metadata (id, name)
        - agents_config is present with at least one agent
        - All agent configurations are valid
        - Optional sub_agents field format if present

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Check required fields
        if not template.id:
            errors.append("Template ID is required")
        if not template.name:
            errors.append("Template name is required")

        # Validate agents_config (required, must have at least one agent)
        if not template.agents_config:
            errors.append("Template must have agents_config with at least one agent")
        else:
            # Validate all agent configurations in agents_config
            for agent_id, agent_config in template.agents_config.items():
                errors.extend(self._validate_agent_config(agent_config, agent_id))

        # Validate sub_agents format (optional field)
        if template.sub_agents is not None:
            if isinstance(template.sub_agents, str):
                if template.sub_agents not in ["all", ""]:
                    errors.append(
                        f"sub_agents string value must be 'all' or empty, got: {template.sub_agents}"
                    )
            elif isinstance(template.sub_agents, list):
                if not all(isinstance(name, str) for name in template.sub_agents):
                    errors.append("sub_agents list must contain only strings")
            else:
                errors.append(
                    "sub_agents must be a string ('all' or empty) or a list of agent names"
                )

        return errors

    def _validate_agent_config(self, agent_config: Any, agent_id: str) -> List[str]:
        """Validate a single agent configuration

        Args:
            agent_config: Agent configuration to validate
            agent_id: Agent identifier (for error messages)

        Returns:
            List of validation errors for this agent
        """
        errors = []

        if not isinstance(agent_config, dict):
            errors.append(f"Agent '{agent_id}' configuration must be a dictionary")
            return errors

        # Check required agent fields
        if "name" not in agent_config or not agent_config["name"]:
            errors.append(f"Agent '{agent_id}' must have a non-empty name")
        if "instructions" not in agent_config or not agent_config["instructions"]:
            errors.append(f"Agent '{agent_id}' must have non-empty instructions")
        if "model" not in agent_config or not agent_config["model"]:
            errors.append(f"Agent '{agent_id}' must have a non-empty model")

        return errors

    def get_default_template(self) -> ChatroomTemplate:
        """Get the default template"""
        default = self.get_template("default")
        if default is None:
            logger.error("Default template not found, creating minimal fallback")
            return self._create_fallback_template()
        return default

    def _create_fallback_template(self) -> ChatroomTemplate:
        """Create a minimal fallback template if default is not available"""
        return ChatroomTemplate(
            id="fallback",
            name="Fallback Template",
            description="Minimal fallback template",
            icon="🔄",
            category="system",
            version="1.0.0",
            agents_config={
                "triage": {
                    "name": "Assistant",
                    "instructions": "You are a helpful AI assistant.",
                    "model": "openai/gpt-5",
                    "icon": "🤖",
                }
            },
            tags=["fallback", "system"],
        )

    def _load_chatrooms(self) -> None:
        """Load unified format chatrooms from chatrooms.yaml

        Extends templates loaded from chatroom_templates.yaml with unified format templates.
        Uses the same _parse_template method for consistency.
        """
        try:
            if not self.chatrooms_path.exists():
                logger.debug(f"Chatrooms file not found: {self.chatrooms_path}")
                return

            with open(self.chatrooms_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data or "chatrooms" not in data:
                logger.debug(f"Chatrooms file empty or invalid: {self.chatrooms_path}")
                return

            chatrooms_data = data.get("chatrooms", {})

            for chatroom_id, chatroom_data in chatrooms_data.items():
                try:
                    # Use unified parsing for all chatroom formats
                    template = self._parse_template(chatroom_id, chatroom_data)

                    if template is None:
                        continue

                    # Handle "all" keyword expansion for sub_agents
                    if self.agents_manager and template.sub_agents == "all":
                        all_agents = self.agents_manager.list_agents()
                        template.sub_agents = all_agents
                        logger.debug(
                            f"Expanded 'all' in chatroom '{chatroom_id}' to {len(all_agents)} agents"
                        )

                    self._templates[chatroom_id] = template
                    logger.debug(f"Loaded chatroom: {chatroom_id}")

                except Exception as e:
                    logger.error(f"Failed to parse chatroom '{chatroom_id}': {e}")

            logger.info(
                f"Loaded {len(chatrooms_data)} chatrooms from {self.chatrooms_path}"
            )

        except Exception as e:
            logger.error(f"Failed to load chatrooms: {e}")

    def resolve_sub_agents_spec(self, sub_agents_spec) -> List[str]:
        """Resolve sub_agents specification to list of agent names from agents.yaml library.

        Args:
            sub_agents_spec: Specification value - can be:
                - "all": Load all agents from agents.yaml
                - [list]: Load specific agents from library
                - None or []: No library agents

        Returns:
            List of agent names to load from library

        Raises:
            ValueError: If spec format is invalid
        """
        if sub_agents_spec == "all":
            if self.agents_manager is None:
                logger.warning(
                    "agents_manager not available, cannot load all sub-agents"
                )
                return []
            agent_names = self.agents_manager.list_agents()
            logger.debug(f"Loading all sub-agents from agents.yaml: {agent_names}")
            return agent_names

        elif isinstance(sub_agents_spec, list):
            logger.debug(f"Loading specific sub-agents: {sub_agents_spec}")
            return sub_agents_spec

        elif sub_agents_spec is None or sub_agents_spec == []:
            logger.debug("No sub-agents specified")
            return []

        else:
            raise ValueError(
                f"sub_agents must be 'all', a list, or null/empty, got: {sub_agents_spec}"
            )

    def collect_agent_configs(
        self, team_template: Dict[str, Any]
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Collect and validate agent configs from template.

        Separates inline agents (from agents_config) and sub-agents (from agents.yaml).

        Args:
            team_template: Template configuration dict with agents_config and optional sub_agents

        Returns:
            Tuple of (inline_agents_config, sub_agents_config)
            - inline_agents_config: Dict of inline agent configs
            - sub_agents_config: Dict of sub-agent configs

        Raises:
            ValueError: If template is invalid (missing agents_config)
        """
        template_name = team_template.get("template_name") or team_template.get(
            "name", "unknown"
        )

        # Get inline agents config (required)
        inline_agents_config = team_template.get("agents_config", {})
        if not inline_agents_config:
            raise ValueError(f"Template '{template_name}' missing agents_config")

        # Get sub-agents config (optional)
        sub_agents_spec = team_template.get("sub_agents")
        sub_agents_config = {}

        if sub_agents_spec and sub_agents_spec != []:
            resolved_sub_agents = self.resolve_sub_agents_spec(sub_agents_spec)
            logger.debug(
                f"Resolved sub_agents spec ('{sub_agents_spec}') to: {resolved_sub_agents}"
            )

            if self.agents_manager:
                for agent_name in resolved_sub_agents:
                    agent_config = self.agents_manager.get_agent_config(agent_name)
                    if agent_config is None:
                        logger.warning(
                            f"Sub-agent '{agent_name}' not found in agents.yaml, skipping"
                        )
                        continue
                    sub_agents_config[agent_name] = agent_config

        logger.debug(
            f"Collected {len(inline_agents_config)} inline agents, "
            f"{len(sub_agents_config)} sub-agents"
        )

        return inline_agents_config, sub_agents_config

    def add_default_services_to_configs(self, agent_configs: Dict[str, Any]) -> None:
        """Add built-in toolsets and MCP servers to agent configs.

        Modifies agent configs in-place, adding BUILTIN_TOOLSETS and BUILTIN_MCP_SERVERS
        if they're not already present.

        Args:
            agent_configs: Dict of agent configurations to update
        """
        for agent_config in agent_configs.values():
            # Add built-in toolsets
            toolsets = agent_config.get("toolsets", [])
            for toolset in BUILTIN_TOOLSETS:
                if toolset not in toolsets:
                    toolsets.append(toolset)
            agent_config["toolsets"] = toolsets

            # Add built-in MCP servers
            mcp_servers = agent_config.get("mcp_servers", [])
            for mcp_server in BUILTIN_MCP_SERVERS:
                if mcp_server not in mcp_servers:
                    mcp_servers.append(mcp_server)
            agent_config["mcp_servers"] = mcp_servers

    def reload_templates(self) -> None:
        """Reload templates from chatrooms.yaml"""
        logger.info("Reloading chatroom templates from chatrooms.yaml")
        self._templates = {}
        self._load_chatrooms()

    # ============================================================================
    # CRUD Operations for Template Management
    # ============================================================================

    def create_template(
        self, template_data: Dict[str, Any]
    ) -> tuple[bool, str, Optional[ChatroomTemplate]]:
        """Create a new template.

        Args:
            template_data: Template data dict with name, description, agents_config, etc.

        Returns:
            Tuple of (success, message, template_object)
        """
        try:
            # Validate required fields
            name = template_data.get("name", "").strip()
            if not name:
                return False, "Template name is required", None

            agents_config = template_data.get("agents_config", {})
            if not agents_config:
                return False, "At least one agent is required in agents_config", None

            # Generate template ID from timestamp
            import time

            template_id = f"tpl_{int(time.time() * 1000)}"

            # Create new template object
            new_template = ChatroomTemplate(
                id=template_id,
                name=name,
                description=template_data.get("description", ""),
                icon=template_data.get("icon", "📋"),
                category=template_data.get("category", "custom"),
                version="1.0.0",
                agents_config=agents_config,
                sub_agents=template_data.get("sub_agents"),
                tags=template_data.get("tags", []),
            )

            # Validate template
            validation_errors = self.validate_template(new_template)
            if validation_errors:
                error_msg = "; ".join(validation_errors)
                return False, f"Template validation failed: {error_msg}", None

            # Store template
            self._templates[template_id] = new_template
            logger.info(f"Created template: {template_id} ({name})")
            return True, template_id, new_template

        except Exception as e:
            logger.error(f"Error creating template: {e}")
            return False, str(e), None

    def update_template(
        self, template_id: str, template_data: Dict[str, Any]
    ) -> tuple[bool, str, Optional[ChatroomTemplate]]:
        """Update an existing template.

        Args:
            template_id: ID of template to update
            template_data: Updated template data

        Returns:
            Tuple of (success, message, template_object)
        """
        try:
            # Check if template exists
            existing = self.get_template(template_id)
            if not existing:
                return False, f"Template '{template_id}' not found", None

            # Merge with existing data
            updated_template = ChatroomTemplate(
                id=template_id,
                name=template_data.get("name", existing.name),
                description=template_data.get("description", existing.description),
                icon=template_data.get("icon", existing.icon),
                category=template_data.get("category", existing.category),
                version=template_data.get("version", existing.version),
                agents_config=template_data.get(
                    "agents_config", existing.agents_config
                ),
                sub_agents=template_data.get("sub_agents", existing.sub_agents),
                tags=template_data.get("tags", existing.tags),
            )

            # Validate updated template
            validation_errors = self.validate_template(updated_template)
            if validation_errors:
                error_msg = "; ".join(validation_errors)
                return False, f"Template validation failed: {error_msg}", None

            # Replace template
            self._templates[template_id] = updated_template
            logger.info(f"Updated template: {template_id}")
            return True, "Template updated successfully", updated_template

        except Exception as e:
            logger.error(f"Error updating template {template_id}: {e}")
            return False, str(e), None

    def delete_template(self, template_id: str) -> tuple[bool, str]:
        """Delete a template.

        Args:
            template_id: ID of template to delete

        Returns:
            Tuple of (success, message)
        """
        try:
            if template_id not in self._templates:
                return False, f"Template '{template_id}' not found"

            del self._templates[template_id]
            logger.info(f"Deleted template: {template_id}")
            return True, "Template deleted successfully"

        except Exception as e:
            logger.error(f"Error deleting template {template_id}: {e}")
            return False, str(e)

    # ============================================================================
    # CRUD Operations for Agent Management
    # ============================================================================

    # Optional fields that can be included in agent config
    _OPTIONAL_AGENT_FIELDS = ["icon", "description", "toolsets", "mcp_servers"]
    # Required fields that must have non-empty values
    _REQUIRED_AGENT_FIELDS = {
        "name": "Agent name is required",
        "instructions": "Agent instructions are required",
        "model": "Agent model is required",
    }

    def _validate_required_agent_fields(
        self, agent_data: Dict[str, Any]
    ) -> Optional[str]:
        """Validate that all required agent fields are present and non-empty.

        Args:
            agent_data: Agent data dict to validate

        Returns:
            Error message if validation fails, None otherwise
        """
        for field, error_msg in self._REQUIRED_AGENT_FIELDS.items():
            value = (
                agent_data.get(field, "").strip()
                if isinstance(agent_data.get(field), str)
                else ""
            )
            if not value:
                return error_msg
        return None

    def _build_agent_config(
        self, agent_data: Dict[str, Any], required_only: bool = False
    ) -> Dict[str, Any]:
        """Build agent config from agent data, filtering valid fields.

        Args:
            agent_data: Source agent data
            required_only: If True, only include required fields; otherwise include optional fields

        Returns:
            Agent config dict with valid fields
        """
        config = {}

        # Always include required fields (with trimming for strings)
        for field in self._REQUIRED_AGENT_FIELDS:
            if field in agent_data:
                value = agent_data[field]
                config[field] = value.strip() if isinstance(value, str) else value

        # Add optional fields if not required_only
        if not required_only:
            for field in self._OPTIONAL_AGENT_FIELDS:
                if field in agent_data and agent_data[field]:
                    config[field] = agent_data[field]

        return config

    def create_agent(
        self, agent_data: Dict[str, Any]
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """Create a new agent.

        Args:
            agent_data: Agent data dict with name, instructions, model, etc.

        Returns:
            Tuple of (success, agent_id, agent_config)
        """
        try:
            # Validate required fields
            validation_error = self._validate_required_agent_fields(agent_data)
            if validation_error:
                return False, validation_error, None

            # Generate agent ID
            import time

            agent_id = f"agent_{int(time.time() * 1000)}"

            # Build agent config
            agent_config = self._build_agent_config(agent_data)

            # Validate agent config
            validation_errors = self.agents_manager.validate_agent_config(agent_config)
            if validation_errors:
                error_msg = "; ".join(validation_errors)
                return False, f"Agent validation failed: {error_msg}", None

            # Store agent
            self.agents_manager._agents[agent_id] = agent_config
            logger.info(f"Created agent: {agent_id} ({agent_config['name']})")
            return True, agent_id, agent_config

        except Exception as e:
            logger.error(f"Error creating agent: {e}")
            return False, str(e), None

    def update_agent(
        self, agent_id: str, agent_data: Dict[str, Any]
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """Update an existing agent.

        Args:
            agent_id: ID of agent to update
            agent_data: Updated agent data

        Returns:
            Tuple of (success, message, agent_config)
        """
        try:
            # Check if agent exists
            existing = self.agents_manager.get_agent_config(agent_id)
            if not existing:
                return False, f"Agent '{agent_id}' not found", None

            # Merge with existing data
            updated_config = {**existing}
            new_config = self._build_agent_config(agent_data)
            updated_config.update(new_config)

            # Validate updated config
            validation_errors = self.agents_manager.validate_agent_config(
                updated_config
            )
            if validation_errors:
                error_msg = "; ".join(validation_errors)
                return False, f"Agent validation failed: {error_msg}", None

            # Update agent
            self.agents_manager._agents[agent_id] = updated_config
            logger.info(f"Updated agent: {agent_id}")
            return True, "Agent updated successfully", updated_config

        except Exception as e:
            logger.error(f"Error updating agent {agent_id}: {e}")
            return False, str(e), None

    def delete_agent(self, agent_id: str) -> tuple[bool, str]:
        """Delete an agent.

        Args:
            agent_id: ID of agent to delete

        Returns:
            Tuple of (success, message)
        """
        try:
            if agent_id not in self.agents_manager._agents:
                return False, f"Agent '{agent_id}' not found"

            del self.agents_manager._agents[agent_id]
            logger.info(f"Deleted agent: {agent_id}")
            return True, "Agent deleted successfully"

        except Exception as e:
            logger.error(f"Error deleting agent {agent_id}: {e}")
            return False, str(e)

    def get_all_agents(self) -> List[Dict[str, Any]]:
        """Get all agents with their IDs included.

        Returns:
            List of agent configurations with 'id' field included
        """
        agents_list = []
        for agent_id, agent_config in self.agents_manager._agents.items():
            agent_dict = {
                "id": agent_id,
                **agent_config,
            }
            agents_list.append(agent_dict)
        return agents_list


# Global template manager instance
_template_manager: Optional[TemplateManager] = None


def get_template_manager() -> TemplateManager:
    """Get the global template manager instance"""
    global _template_manager
    if _template_manager is None:
        _template_manager = TemplateManager()
    return _template_manager
