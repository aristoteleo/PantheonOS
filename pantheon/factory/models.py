"""
Data Models for Pantheon Templates

Unified data structures for agents and chatrooms.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class AgentConfig:
    """
    Agent configuration - used for both standalone agents and agents within chatrooms.
    Unified structure for all agent definitions.
    """
    id: str
    name: str
    model: str
    icon: str = "🤖"
    instructions: str = ""
    toolsets: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'model': self.model,
            'icon': self.icon,
            'instructions': self.instructions,
            'toolsets': self.toolsets,
            'mcp_servers': self.mcp_servers,
            'tags': self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AgentConfig':
        """Create from dictionary"""
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            model=data.get('model', ''),
            icon=data.get('icon', '🤖'),
            instructions=data.get('instructions', ''),
            toolsets=data.get('toolsets', []),
            mcp_servers=data.get('mcp_servers', []),
            tags=data.get('tags', []),
        )

    def to_creation_payload(self) -> dict:
        """Payload dict for create_agent helper."""
        return {
            'name': self.name,
            'instructions': self.instructions,
            'model': self.model,
            'icon': self.icon,
            'toolsets': list(self.toolsets or []),
            'mcp_servers': list(self.mcp_servers or []),
        }


@dataclass
class TeamConfig:
    """
    Chatroom configuration.

    - agents: List of agents defined within this chatroom
    - sub_agents: List of agent IDs referenced from the agents library
    """
    id: str
    name: str
    description: str
    icon: str = "💬"
    category: str = "general"
    version: str = "1.0.0"
    agents: List[AgentConfig] = field(default_factory=list)
    sub_agents: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=lambda: ["none"])

    @property
    def all_agents(self) -> List[str]:
        """Get all agent IDs (internal + referenced)"""
        return [a.id for a in self.agents] + self.sub_agents

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'icon': self.icon,
            'category': self.category,
            'version': self.version,
            'agents': [a.to_dict() for a in self.agents],
            'sub_agents': self.sub_agents,
            'tags': self.tags,
            'skills': list(self.skills or ["none"]),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TeamConfig':
        """Create from dictionary"""
        agents = []
        if 'agents' in data and isinstance(data['agents'], list):
            agents = [AgentConfig.from_dict(a) if isinstance(a, dict) else a
                     for a in data['agents']]

        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            description=data.get('description', ''),
            icon=data.get('icon', '💬'),
            category=data.get('category', 'general'),
            version=data.get('version', '1.0.0'),
            agents=agents,
            sub_agents=data.get('sub_agents', []),
            tags=data.get('tags', []),
            skills=normalize_skills_value(data.get('skills', 'none')),
        )


def normalize_skills_value(skills_value) -> list[str]:
    """Normalize inbound skills specification into a list form."""
    if skills_value is None:
        return ["none"]

    if isinstance(skills_value, str):
        normalized = skills_value.strip()
        if not normalized:
            return ["none"]
        normalized_lower = normalized.lower()
        if normalized_lower in {"all", "none"}:
            return [normalized_lower]
        return [normalized]

    if isinstance(skills_value, list):
        cleaned: list[str] = []
        for entry in skills_value:
            if entry is None:
                continue
            text = str(entry).strip()
            if text:
                cleaned.append(text.lower() if text.lower() in {"all", "none"} else text)
        return cleaned or ["none"]

    return ["none"]
