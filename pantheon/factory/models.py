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
    description: str = ""
    icon: str = "🤖"
    instructions: str = ""
    toolsets: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    source_path: Optional[str] = None
    scope: str = "project"
    # The Apps this agent may use, {allow?, deny?} (docs/pantheonos-app-interface.md).
    # None with toolsets: the legacy selection those toolsets make.
    apps: Optional[dict] = None

    def app_selection(self) -> Optional[dict]:
        """The agent's apps filter. An agent that names neither apps nor
        toolsets of Apps may use every App; one whose toolsets name Apps
        keeps their legacy selection (None). Built-ins and MCP servers in
        toolsets are not Apps."""
        if self.apps is not None:
            return self.apps
        named = [t for t in self.toolsets or [] if t not in ("task", "think", "skills")
                 and t != "mcp" and not t.startswith("mcp:")]
        return None if named else {}

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "description": self.description,
            "icon": self.icon,
            "instructions": self.instructions,
            "toolsets": self.toolsets,
            "mcp_servers": self.mcp_servers,
            "tags": self.tags,
            "source_path": self.source_path,
            "scope": self.scope,
            **({"apps": self.apps} if self.apps is not None else {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        """Create from dictionary"""
        toolsets = list(data.get("toolsets", []) or [])
        # Backward compat: absorb legacy think_tool flag into toolsets
        if data.get("think_tool") and "think" not in toolsets:
            toolsets.append("think")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            model=data.get("model", ""),
            description=data.get("description", ""),
            icon=data.get("icon", "🤖"),
            instructions=data.get("instructions", ""),
            toolsets=toolsets,
            mcp_servers=data.get("mcp_servers", []),
            tags=data.get("tags", []),
            source_path=data.get("source_path"),
            apps=cls._apps(data.get("apps")),
        )

    @staticmethod
    def _apps(value) -> Optional[dict]:
        if value is None:
            return None
        from pantheon.agent_pantheonos import AppFilter
        return AppFilter.check(value)

    def to_creation_payload(self) -> dict:
        """Payload dict for create_agent helper."""
        apps = self.app_selection()
        return {
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "model": self.model,
            "icon": self.icon,
            "toolsets": list(self.toolsets or []) if apps is None else
                        [t for t in self.toolsets or [] if t == "mcp" or t.startswith("mcp:")],
            "mcp_servers": list(self.mcp_servers or []),
            **({"apps": apps} if apps is not None else {}),
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
    source_path: Optional[str] = None
    scope: str = "project"
    # "team": every agent converses and may transfer (legacy).
    # "agent": the first agent is the only one the user talks to; the others
    # are sub-agent profiles it can delegate to with one call_agent call, and
    # every agent sees only its core tools (other App tools on demand).
    mode: str = "team"

    @property
    def all_agents(self) -> List[str]:
        """Get all agent IDs"""
        return [a.id for a in self.agents]

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "version": self.version,
            "agents": [a.to_dict() for a in self.agents],
            "tags": self.tags,
            "source_path": self.source_path,
            "scope": self.scope,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeamConfig":
        """Create from dictionary"""
        agents = []
        if "agents" in data and isinstance(data["agents"], list):
            agents = [
                AgentConfig.from_dict(a) if isinstance(a, dict) else a
                for a in data["agents"]
            ]

        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            icon=data.get("icon", "💬"),
            category=data.get("category", "general"),
            version=data.get("version", "1.0.0"),
            agents=agents,
            tags=data.get("tags", []),
            source_path=data.get("source_path"),
            mode=data.get("mode") or "team",
        )
