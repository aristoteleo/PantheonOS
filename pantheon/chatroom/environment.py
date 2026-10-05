"""Explicit composition inputs for Agent execution, not platform RPC mixins.

The composition root supplies a project view and execution factories. This is
an in-process boundary, not a security grant: the App host and dependency SDK
must still enforce credentials, filesystem scope and provider generations.
No default silently falls back to the platform registry or owner credentials.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


class ProjectInfo(Protocol):
    name: str
    path: str


class ProjectView(Protocol):
    """Read-only view supplied to Agent; registration/selection belongs outside."""

    @property
    def active_project(self) -> ProjectInfo | None: ...

    @property
    def default_project(self) -> ProjectInfo | None: ...

    def list_projects(self) -> list[dict]: ...

    def get_project(self, path: str) -> ProjectInfo | None: ...


class AgentFactory(Protocol):
    async def __call__(self, configs: dict, *, conversation_id: str | None = None) -> list: ...


@dataclass(frozen=True)
class AgentEnvironment:
    projects: ProjectView
    templates: Any
    settings: Callable[[], Any]
    ensure_services: Callable[[str, list[str]], Awaitable[None]]
    create_agents: AgentFactory
    validate_model: Callable[[str], tuple[bool, str]]
    close_agents: Callable[[], Awaitable[None]] | None = None
    # The composition constructs/rolls back the full plugin set. Any borrowed
    # dependency clients are disposed by close_agents after plugin drain.
    create_plugins: Callable[[], Awaitable[list]] | None = None
    # App-owned conversations need not live alongside shared project files.
    # None retains the existing CLI/Desktop <project>/.pantheon/memory layout.
    project_memory_dir: Callable[[str], str] | None = None
    # Compatibility compositions may lease a store before it is opened. New
    # Agent Apps already hold their own data-namespace writer lock.
    acquire_memory_store: Callable[[str], None] | None = None
    # App-owned defaults must be applied before dependency preflight as well as
    # before reserving a member revision. None preserves legacy CLI behaviour.
    prepare_agent_configs: Callable[[dict], dict] | None = None
    # Temporary local previews are runtime-owned, not a grant to write the
    # registered workspace (which may belong to another Fleet node).
    # None preserves the legacy CLI/Desktop project-local preview directory.
    image_output_dir: Callable[[str], str] | None = None
    # Auxiliary chat helpers share only this App's authorized model scope.
    # None is the explicit legacy CLI/Desktop composition.
    model_scope: Any = None
