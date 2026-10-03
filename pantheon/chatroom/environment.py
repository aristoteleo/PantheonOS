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


@dataclass(frozen=True)
class AgentEnvironment:
    projects: ProjectView
    templates: Any
    settings: Callable[[], Any]
    ensure_services: Callable[[str, list[str]], Awaitable[None]]
    create_agents: Callable[[dict], Awaitable[list]]
    validate_model: Callable[[str], tuple[bool, str]]
