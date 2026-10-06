"""Admission for the whole Agent-owned data tree, before opening its databases.

Version 1 names the existing extracted-App layout. Earlier unmarked data uses
that same layout; its existing migration and database admission still run before
we publish a marker. Changing this version requires a real App-owned migration,
not simply allowing a new number here.
"""
import json
import os
from pathlib import Path
import stat
import tempfile

FORMAT_FILE = 'agent-data-format.json'
FORMAT_ID = 'pantheon-agent'
FORMAT_VERSION = 1


def check_format(root, namespace):
    path = Path(root) / FORMAT_FILE
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('Agent data format marker must be a regular file') from None
        return False
    except OSError:
        raise ValueError('Agent data format marker cannot be opened') from None
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Agent data format marker must be a regular file')
        raw = stream.read(4097)
    try:
        value = json.loads(raw) if len(raw) <= 4096 else None
        if (not isinstance(value, dict) or set(value) != {'id', 'version', 'namespace'}
                or type(value['version']) is not int or value['version'] != FORMAT_VERSION
                or value['id'] != FORMAT_ID or value['namespace'] != namespace):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('Unsupported Agent data format or namespace; migration is required') from None
    return True


def stamp_format(root, namespace):
    """Call under data admission and instance writer locks after legacy checks."""
    fd, temporary = tempfile.mkstemp(prefix='.agent-format-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'id': FORMAT_ID, 'version': FORMAT_VERSION, 'namespace': namespace}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, Path(root) / FORMAT_FILE)
        if os.name == 'posix':
            directory = os.open(root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
