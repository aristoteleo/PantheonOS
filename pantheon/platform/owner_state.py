"""Explicit persistent storage for platform-owned operation journals.

The deployment must mount this directory durably. This is not a snapshot or
distributed fencing implementation, and never imports another owner's journals.
"""
import os
from pathlib import Path

from pantheon.apps.owner_journal import OwnerJournal


def configured_directory(value=None):
    value = value if value is not None else os.environ.get('PANTHEON_PLATFORM_STATE_DIR')
    if value is None:
        return None  # Preserve the legacy HOME location until explicitly moved.
    path = Path(value)
    if not str(value).strip() or not path.is_absolute() or '..' in path.parts:
        raise ValueError('Platform state needs an explicit absolute persistent directory')
    return path


def prepare_directory(path):
    if path is None:
        return
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    OwnerJournal(path)._private(path, directory=True)
