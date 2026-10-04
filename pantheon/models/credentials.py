"""Owner-side access to the existing Fleet model credential vault.

Used by model provisioning and legacy migration, never shipped in an Agent App.
No management RPC or second secret store: Fleet owns persistence and conflicts.
"""
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.parse import urlsplit


def _regular_file(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
    try:
        info, actual = os.fstat(fd), path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_ISLNK(actual.st_mode)
                or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino)):
            raise ValueError('Fleet node identity must be a regular unlinked file')
        return fd
    except BaseException:
        os.close(fd)
        raise


def model_credential_endpoint(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError('Supply an explicit model API endpoint')
    parts = urlsplit(value)
    if (not parts.hostname or parts.username or parts.password or '?' in value or '#' in value
            or parts.scheme not in ('http', 'https')
            or parts.scheme == 'http' and parts.hostname not in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Model credentials require HTTPS or a local endpoint')
    parts.port
    # The vault normalizes its lookup identity; a native SDK's actual API base
    # must retain the source path. Adding /v1 here changes the request URL.
    return value.rstrip('/')


class LocalModelCredentialVault:
    """A specifically selected local Fleet vault; never a management RPC."""
    def __init__(self, executable, *, state_dir, owner, node_id):
        if (not Path(executable).is_absolute() or not Path(state_dir).is_absolute()
                or not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner)
                or not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', node_id)):
            raise ValueError('Supply the local Fleet executable, state root and exact owner/node')
        self.executable, self.state_dir = Path(executable), Path(state_dir)
        self.owner, self.node_id = owner, node_id
        self._check_node()

    def _check_node(self):
        fd = _regular_file(self.state_dir / 'node_id')
        with os.fdopen(fd, 'rb') as stream:
            value = stream.read(257)
        if value.strip() != self.node_id.encode():
            raise ValueError('Credential vault belongs to another Fleet node')

    def ensure(self, ref, endpoint, key):
        self._check_node()
        if not isinstance(ref, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref):
            raise ValueError('Invalid model credential reference')
        if not isinstance(key, str) or not 0 < len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError('Invalid model API credential')
        endpoint = model_credential_endpoint(endpoint)
        try:
            result = subprocess.run([str(self.executable), 'credentials', 'ensure',
                '--state-dir', str(self.state_dir), '--fleet', self.owner,
                '--name', ref.removeprefix('node-secret://'), '--endpoint', endpoint, '--stdin'],
                input=key.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('Could not provision the local Fleet model credential') from None
        if result.returncode:
            raise ValueError('Fleet credential is unavailable or conflicts; it was not replaced')
