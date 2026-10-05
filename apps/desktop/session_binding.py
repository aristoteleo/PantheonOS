"""An explicitly owned desktop document, presence registry and event connection.

The state directory belongs to the desktop, not to a consuming Agent. A second
service attached to the same desktop uses the same directory and event namespace,
but owns its own publisher connection. Shutdown closes that connection without
removing the shared window document or signing other viewports out.
"""
from pathlib import Path

from .desktop_session import DesktopSessionStore
from .presence import PresenceStore


class DesktopSessionBinding:
    def __init__(self, state_root: Path, publisher):
        root = Path(state_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError('Desktop state root must be an existing absolute directory')
        if not callable(getattr(publisher, 'publish_stream', None)) or not callable(getattr(publisher, 'close', None)):
            raise ValueError('Desktop needs an owned event publisher')
        self.state_root = root.resolve()
        self.document = DesktopSessionStore(work_dir=self.state_root)
        self.presence = PresenceStore(work_dir=self.state_root)
        self.publisher = publisher
