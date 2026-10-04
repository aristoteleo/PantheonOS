"""Import guard installed in every Python process of the desktop Fleet gate."""
import importlib.abc
import os
from pathlib import Path
import sys


class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == name or fullname.startswith(name + '.') for name in (
                'pantheon.agent', 'pantheon.chatroom', 'pantheon.team',
                'pantheon.factory', 'pantheon.internal.learning_system', 'pantheon.internal.memory')):
            message = 'Desktop platform imported Agent: ' + fullname
            record(message)
            raise AssertionError(message)


def record(message):
    path = os.environ.get('PANTHEON_TEST_IMPORT_AUDIT')
    if path:
        with Path(path).open('a') as out:
            out.write(f'{os.getpid()} {message}\n')


def install():
    sys.meta_path.insert(0, NoAgent())
    record('guard installed')
