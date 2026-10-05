"""Explicit workspace, App catalog and served roots for a Desktop deployment."""
from pathlib import Path

from .data_server import LiveViewDataServer


class DesktopFilesBinding:
    def __init__(self, *, workspace: Path, app_roots, data_roots, server: LiveViewDataServer):
        def absolute(value):
            path = Path(value)
            if not path.is_absolute():
                raise ValueError('Desktop roots must be absolute')
            return path.resolve()
        self.workspace = absolute(workspace)
        if not self.workspace.is_dir():
            raise ValueError('Desktop workspace must exist')
        self.app_roots = tuple((absolute(path), scope) for path, scope in app_roots)
        if any(scope not in {'workspace', 'user', 'builtin'} for _, scope in self.app_roots):
            raise ValueError('Unsupported Desktop catalog scope')
        if sum(scope == 'user' for _, scope in self.app_roots) != 1:
            raise ValueError('Desktop Store requires exactly one user catalog root')
        self.data_roots = tuple(dict.fromkeys(absolute(path) for path in data_roots))
        if not isinstance(server, LiveViewDataServer):
            raise TypeError('Desktop needs an owned LiveViewDataServer')
        self.server = server
