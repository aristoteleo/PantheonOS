"""The triage of every shipped ToolSet into the App model (P1 of §08).

Each surviving toolset class is classified once, here, and this table is what
the unified registry serves. Kinds:

  service    a real App: gets a reflected headless manifest
  plugin     agent-pipeline hook wearing a toolset's coat (runs embedded in
             the brain; still an App, flagged so the loader knows)
  component  an internal part of another App — no App of its own
  alias      a legacy class name kept for saved configs — not an App
  absorb     scheduled for deletion into other machinery (still a service
             App until that lands; `absorb_into` names the destination)

`runtime` is today's target execution form (§04c); `builtin_target=True`
marks the Go-rewrite batch (compiled into the fleet runner) — it stays
`process` until the Go implementation passes signature parity.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogEntry:
    app_id: str
    class_name: str
    module: str                      # import path holding the class
    kind: str                        # service | plugin | component | alias | absorb
    runtime: str = "process"         # embedded | process | builtin
    requires: tuple[str, ...] = ()
    prefer: tuple[str, ...] = ()
    #: Initial interface contracts (§06): (name, version, member tool names).
    #: Members may be hidden tools — an interface protecting the frontend's
    #: bus contract is exactly as load-bearing as one protecting the LLM's.
    interfaces: tuple[tuple[str, int, tuple[str, ...]], ...] = ()
    description: str = ""
    parent: str | None = None        # for kind=component
    absorb_into: str | None = None   # for kind=absorb
    builtin_target: bool = False     # Go-rewrite batch
    notes: str = ""

    @property
    def service_type(self) -> str:
        """The snake_case service name templates use ('file_manager'),
        derived from the class name the same way the endpoint's
        _get_toolset_class reverses it."""
        import re
        name = self.class_name.removesuffix("ToolSet")
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


CATALOG: tuple[CatalogEntry, ...] = (
    # ---- body-side services (need the sandbox) ----------------------------
    CatalogEntry(
        "python-interpreter", "PythonInterpreterToolSet", "pantheon.apps.builtin.python",
        kind="service", requires=("proc", "fs:workspace"), prefer=("sandbox",),
        description="Execute Python in the workspace (Jupyter-kernel backed).",
    ),
    CatalogEntry(
        "shell", "ShellToolSet", "pantheon.apps.builtin.shell",
        kind="service", requires=("proc", "fs:workspace"), prefer=("sandbox",),
        builtin_target=True,
        interfaces=(("shell", 1, ("run_command", "new_shell", "run_command_in_shell",
                                  "get_shell_output", "close_shell")),),
        description="Run shell commands in the workspace.",
    ),
    CatalogEntry(
        "pty", "PtyToolSet", "pantheon.apps.builtin.pty",
        kind="service", requires=("proc", "fs:workspace"), prefer=("sandbox",),
        builtin_target=True,
        interfaces=(("pty", 1, ("pty_open", "pty_attach", "pty_write", "pty_resize",
                                "pty_list", "pty_close")),),
        description="Interactive terminal sessions (the Terminal app's backend).",
    ),
    CatalogEntry(
        "file-manager", "FileManagerToolSet", "pantheon.apps.builtin.file",
        kind="service", requires=("fs:workspace",), prefer=("sandbox",),
        builtin_target=True,
        # fs@1 is the Go-implementable core; the tree-sitter outline lives in
        # its own interface so a runner-builtin node can honestly claim fs@1
        # without carrying cgo grammars (outline stays python-only for now).
        interfaces=(("fs", 1, ("read_file", "write_file", "update_file", "glob",
                               "grep", "apply_patch")),
                    ("outline", 1, ("view_file_outline",))),
        description="Workspace file operations, outlines and symbol reads.",
    ),
    CatalogEntry(
        "integrated-notebook", "IntegratedNotebookToolSet", "pantheon.apps.builtin.notebook",
        kind="service", requires=("proc", "fs:workspace"), prefer=("sandbox",),
        description="Notebook editing and execution with streaming.",
    ),
    CatalogEntry(
        "desktop", "DesktopToolSet", "pantheon.apps.builtin.desktop",
        kind="service", requires=("proc", "fs:workspace", "display"), prefer=("sandbox",),
        description="The user's desktop: windows, apps, browser, data server.",
    ),
    CatalogEntry(
        "evolution", "EvolutionToolSet", "pantheon.apps.builtin.evolution",
        kind="service", requires=("proc", "fs:workspace"), prefer=("sandbox",),
        description="Evolutionary experiment runs.",
    ),
    CatalogEntry(
        "mcp-gateway", "MCPGatewayToolSet", "pantheon.apps.builtin.mcp",
        kind="service", requires=("proc", "net"), prefer=("sandbox",),
        interfaces=(("mcp", 1, ("get_uri", "list_servers", "get_server",
                                "start_servers", "stop_servers")),),
        description="Unified MCP gateway: configured MCP servers behind one "
                    "HTTP URI.",
        notes="was the endpoint's mcp_manager; spawns stdio servers, hence proc",
    ),
    # ---- brain-side services (network only; embed in the agent process) ---
    CatalogEntry(
        "web", "WebToolSet", "pantheon.apps.builtin.web",
        kind="service", runtime="embedded", requires=("net",),
        description="Web search and page fetch.",
        notes="second Go-builtin batch candidate",
    ),
    CatalogEntry(
        "scraper", "ScraperToolSet", "pantheon.apps.builtin.scraper",
        kind="service", runtime="embedded", requires=("net",),
        description="ScraperAPI search/scrape (API key required).",
        notes="second Go-builtin batch candidate",
    ),
    CatalogEntry(
        "image-generation", "ImageGenerationToolSet", "pantheon.apps.builtin.image",
        kind="service", runtime="embedded", requires=("net",),
        description="Image generation via model APIs.",
    ),
    CatalogEntry(
        "fleet", "FleetToolSet", "pantheon.apps.builtin.fleet",
        kind="service", runtime="embedded", requires=("net",),
        absorb_into="fleet-runner builtins",
        description="Observe and drive the user's fleet of compute nodes.",
        notes="brain keeps an embedded client; node-side tools become runner builtins",
    ),
    # ---- agent plugins (embedded by nature) --------------------------------
    CatalogEntry(
        "task", "TaskToolSet", "pantheon.apps.builtin.task",
        kind="plugin", runtime="embedded",
        description="Modal-workflow (PLANNING/EXECUTION/VERIFICATION) boundaries "
                    "and prompt shaping. In-process agent pipeline hooks.",
        notes='"Modal" here means workflow modes, not the Modal cloud — no SDK involved',
    ),
    # ---- scheduled absorptions --------------------------------------------
    CatalogEntry(
        "file-transfer", "FileTransferToolSet", "pantheon.apps.builtin.file_transfer",
        kind="absorb", requires=("fs:workspace", "net"),
        absorb_into="fleet transfer (data plane)",
        description="Chunked file transfer to the frontend.",
    ),
    # ---- components (parts of other Apps; no App of their own) ------------
    CatalogEntry(
        "jupyter-kernel", "JupyterKernelToolSet", "pantheon.apps.builtin.notebook",
        kind="component", parent="integrated-notebook",
    ),
    CatalogEntry(
        "notebook-contents", "NotebookContentsToolSet", "pantheon.apps.builtin.notebook",
        kind="component", parent="integrated-notebook",
    ),
    CatalogEntry(
        "evaluator", "EvaluatorToolSet", "pantheon.apps.builtin.evolution",
        kind="component", parent="evolution",
    ),
    # ---- legacy aliases ----------------------------------------------------
    CatalogEntry(
        "live-view", "LiveViewToolSet", "pantheon.apps.builtin.desktop",
        kind="alias", parent="desktop",
        notes="saved agent configs may still name the old class",
    ),
)


def entries(kind: str | None = None) -> tuple[CatalogEntry, ...]:
    if kind is None:
        return CATALOG
    return tuple(e for e in CATALOG if e.kind == kind)


def app_entries() -> tuple[CatalogEntry, ...]:
    """Entries that become Apps: services, plugins, and pending absorptions."""
    return tuple(e for e in CATALOG if e.kind in ("service", "plugin", "absorb"))


def by_class_name() -> dict[str, CatalogEntry]:
    return {e.class_name: e for e in CATALOG}


def by_service_type() -> dict[str, CatalogEntry]:
    """Template service names ('shell', 'file_manager') -> catalog app entry."""
    return {e.service_type: e for e in app_entries()}
