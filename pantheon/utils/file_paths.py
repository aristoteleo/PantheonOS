"""Resolve workspace file references consistently across tools and viewers."""

from pathlib import Path


def resolve_workspace_path(file_path: str, workspace: Path) -> Path:
    """Resolve home/absolute paths and the workspace > user App namespace.

    `.pantheon/apps/<id>` can name a workspace install or a user install.
    Only fall back when the workspace has no such App directory at all:
    a missing file inside a workspace App must never select another copy.
    Unrelated absolute paths, traversal and broken symlinks stay untouched.
    """
    original = Path(file_path).expanduser()
    path = original if original.is_absolute() else workspace / original
    if path.exists() or path.is_symlink() or ".." in original.parts:
        return path
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return path
    if len(relative.parts) < 3 or relative.parts[:2] != (".pantheon", "apps"):
        return path
    app_id = relative.parts[2]
    local_app = workspace / ".pantheon" / "apps" / app_id
    if local_app.exists() or local_app.is_symlink():
        return path
    user_app = Path.home() / ".pantheon" / "apps" / app_id
    if (user_app / "app.json").is_file() or (user_app / "atrium.json").is_file():
        return user_app.joinpath(*relative.parts[3:])
    return path
