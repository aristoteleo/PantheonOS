"""An explicitly owned desktop document, presence registry and event connection.

The state directory belongs to the desktop, not to a consuming Agent. A second
service attached to the same desktop uses the same directory and event namespace,
but owns its own publisher connection. Shutdown closes that connection without
removing the shared window document or signing other viewports out.

With `desktop_state` (a client of the controller's desktop-state service, bound
by the deployment) the window document lives there instead
(docs/os/desktop-state.md); the record in the state root and the settings and
appearance files in the user's workspaces are imported once and no longer
written.
"""
from pathlib import Path

from .desktop_session import RECORD, DesktopSessionStore, RemoteDesktopSession
from .presence import PresenceStore


class DesktopSessionBinding:
    def __init__(self, state_root: Path, publisher, *, desktop_state=None, workspaces=()):
        root = Path(state_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError('Desktop state root must be an existing absolute directory')
        if not callable(getattr(publisher, 'publish_stream', None)) or not callable(getattr(publisher, 'close', None)):
            raise ValueError('Desktop needs an owned event publisher')
        if desktop_state is not None and not callable(getattr(desktop_state, 'invoke', None)):
            raise ValueError('Desktop state needs a dependency client')
        self.state_root = root.resolve()
        # The service client, also used for the installed-App index (app_index.py).
        self.desktop_state = desktop_state
        if desktop_state is not None:
            self.document = RemoteDesktopSession(desktop_state, record=self.state_root / RECORD, workspaces=workspaces)
        else:
            self.document = DesktopSessionStore(work_dir=self.state_root)
        self.presence = PresenceStore(work_dir=self.state_root)
        self.publisher = publisher
