"""
Data Models for Pantheon Templates

Unified data structures for agents and teams.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class AgentConfig:
    """
    Agent configuration - used for both standalone agents and agents within teams.
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
    Team configuration.

    - agents: List of agents defined within this team
    """
    id: str
    name: str
    description: str
    icon: str = "💬"
    category: str = "general"
    version: str = "1.0.0"
    agents: List[AgentConfig] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    @property
    def all_agents(self) -> List[str]:
        """Get all agent IDs"""
        return [a.id for a in self.agents]

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
            'tags': self.tags,
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
            tags=data.get('tags', []),
        )
