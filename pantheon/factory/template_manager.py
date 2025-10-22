"""
Template Manager for Chatroom Templates
Handles loading, validation, and management of chatroom templates
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from ..utils.log import logger


@dataclass
class ChatroomTemplate:
    """Represents a chatroom template with metadata and configuration"""

    id: str
    name: str
    description: str
    icon: str
    category: str
    version: str
    agents_config: Dict[str, Any]
    tags: List[str]

    @property
    def required_toolsets(self) -> List[str]:
        """Dynamically compute all toolsets required by agents in this template"""
        toolsets = set()
        for agent_config in self.agents_config.values():
            if isinstance(agent_config, dict) and "toolsets" in agent_config:
                toolsets.update(agent_config["toolsets"])
        return sorted(list(toolsets))

    @property
    def required_mcp_servers(self) -> List[str]:
        """Dynamically compute all MCP servers required by agents in this template"""
        mcp_servers = set()
        for agent_config in self.agents_config.values():
            if isinstance(agent_config, dict) and "mcp_servers" in agent_config:
                mcp_servers.update(agent_config["mcp_servers"])
        return sorted(list(mcp_servers))

    def to_dict(self) -> Dict[str, Any]:
        """Convert template to dictionary format"""
        return {
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


class TemplateManager:
    """Manages chatroom templates including loading, validation, and providing access"""

    def __init__(self, templates_path: Optional[str] = None):
        """
        Initialize template manager

        Args:
            templates_path: Path to templates YAML file. If None, uses default path.
        """
        if templates_path is None:
            templates_path = os.path.join(
                os.path.dirname(__file__), "chatroom_templates.yaml"
            )

        self.templates_path = Path(templates_path)
        self._templates: Dict[str, ChatroomTemplate] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """Load templates from YAML file"""
        try:
            if not self.templates_path.exists():
                logger.error(f"Templates file not found: {self.templates_path}")
                return

            with open(self.templates_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data or "templates" not in data:
                logger.error("Invalid template file format")
                return

            templates_data = data["templates"]
            self._templates = {}

            for template_id, template_data in templates_data.items():
                try:
                    template = self._parse_template(template_id, template_data)
                    self._templates[template_id] = template
                    logger.debug(f"Loaded template: {template_id}")
                except Exception as e:
                    logger.error(f"Failed to parse template {template_id}: {e}")

            logger.info(f"Loaded {len(self._templates)} chatroom templates")

        except Exception as e:
            logger.error(f"Failed to load templates: {e}")

    def _parse_template(
        self, template_id: str, data: Dict[str, Any]
    ) -> ChatroomTemplate:
        """Parse template data into ChatroomTemplate object"""
        return ChatroomTemplate(
            id=data.get("id", template_id),
            name=data.get("name", template_id),
            description=data.get("description", ""),
            icon=data.get("icon", "🏠"),
            category=data.get("category", "general"),
            version=data.get("version", "1.0.0"),
            agents_config=data.get("agents_config", {}),
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
        """
        Validate template configuration

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Check required fields
        if not template.id:
            errors.append("Template ID is required")
        if not template.name:
            errors.append("Template name is required")
        if not template.agents_config:
            errors.append("Template must have at least one agent")

        # Validate triage agent exists
        if "triage" not in template.agents_config:
            errors.append("Template must have a 'triage' agent")

        # Validate agent configurations
        for agent_id, agent_config in template.agents_config.items():
            if not isinstance(agent_config, dict):
                errors.append(f"Agent '{agent_id}' configuration must be a dictionary")
                continue

            # Check required agent fields
            if "name" not in agent_config:
                errors.append(f"Agent '{agent_id}' must have a name")
            if "instructions" not in agent_config:
                errors.append(f"Agent '{agent_id}' must have instructions")
            if "model" not in agent_config:
                errors.append(f"Agent '{agent_id}' must have a model")

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

    def reload_templates(self) -> None:
        """Reload templates from file"""
        logger.info("Reloading chatroom templates")
        self._load_templates()


# Global template manager instance
_template_manager: Optional[TemplateManager] = None


def get_template_manager() -> TemplateManager:
    """Get the global template manager instance"""
    global _template_manager
    if _template_manager is None:
        _template_manager = TemplateManager()
    return _template_manager
