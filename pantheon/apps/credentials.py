"""Owner-side access to the existing endpoint-bound Fleet App credential vault.

Shared by ordinary App provisioning and model compatibility adapters.
Never shipped in an Agent App; only the deployment owner can deliver credentials.
Fleet owns persistence and conflicts. Remote delivery is owner-only and encrypted.
"""
import os
import ipaddress
from pathlib import Path
import re
import stat
import subprocess
import asyncio
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


def app_credential_endpoint(value):
    """Validate the endpoint presented to the App, without API path rewriting."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError('Supply an explicit App credential endpoint')
    parts = urlsplit(value)
    if (not parts.hostname or parts.username is not None or parts.password is not None
            or '?' in value or '#' in value or parts.scheme not in ('http', 'https', 'nats', 'tls', 'ws', 'wss')):
        raise ValueError('Invalid App credential endpoint')
    port = parts.port
    if port is not None and not 0 < port <= 65535:
        raise ValueError('Invalid App credential port')
    local = parts.hostname == 'localhost'
    if not local:
        try:
            local = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            pass
    if parts.scheme in ('http', 'nats', 'ws') and not local:
        raise ValueError('App credentials require an encrypted transport outside loopback')
    if parts.scheme in ('nats', 'tls') and parts.path.rstrip('/'):
        raise ValueError('NATS credentials cannot name an HTTP path')
    if parts.scheme == 'http' and parts.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('HTTP credentials require the node vault loopback endpoint')
    return value if parts.scheme in ('ws', 'wss') else value.rstrip('/')


def credential_identity(endpoint):
    """Match the existing Go vault identity; never use this as an App URL."""
    parts = urlsplit(endpoint)
    if parts.scheme in ('http', 'https') and not parts.path:
        return endpoint + '/v1'
    return endpoint


class LocalAppCredentialVault:
    """A specifically selected local Fleet vault; never a management RPC."""
    validate_endpoint = staticmethod(app_credential_endpoint)

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

    async def check_async(self):
        await asyncio.to_thread(self._check_node)

    async def ensure_async(self, ref, endpoint, key):
        from pantheon.apps.dependency_binding_client import _drain
        await _drain(asyncio.create_task(asyncio.to_thread(self.ensure, ref, endpoint, key)))

    def ensure(self, ref, endpoint, key):
        self._check_node()
        if not isinstance(ref, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref):
            raise ValueError('Invalid App credential reference')
        if not isinstance(key, str) or not 0 < len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError('Invalid App credential')
        endpoint = self.validate_endpoint(endpoint)
        try:
            result = subprocess.run([str(self.executable), 'credentials', 'ensure',
                '--state-dir', str(self.state_dir), '--fleet', self.owner,
                '--name', ref.removeprefix('node-secret://'), '--endpoint', endpoint, '--stdin'],
                input=key.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('Could not provision the local Fleet App credential') from None
        if result.returncode:
            raise ValueError('Fleet credential is unavailable or conflicts; it was not replaced')


class RemoteAppCredentialVault:
    """Owner-authorized delivery to one node, using its existing credential vault.

    Fleet authenticates challenge discovery. Ephemeral encryption keeps the key
    out of transport/operation records; it does not replace owner authentication.
    A lost reply can be retried with a fresh challenge and the same credential.
    Neither a conflict nor a timeout authorizes replacement or rotation.
    """
    validate_endpoint = staticmethod(app_credential_endpoint)

    def __init__(self, lifecycle, *, owner, node_id):
        from pantheon.apps.lifecycle import FleetLifecycle
        if (not isinstance(lifecycle, FleetLifecycle)
                or not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner)
                or not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', node_id)):
            raise ValueError('Supply an authenticated Fleet lifecycle and exact owner/node')
        self.lifecycle, self.owner, self.node_id = lifecycle, owner, node_id

    async def check_async(self):
        try:
            async with asyncio.timeout(30):
                state = await self.lifecycle.status(self.node_id)
            if (not isinstance(state, dict) or state.get('owner') != self.owner
                    or state.get('node_id') != self.node_id
                    or type(state.get('credential_import_protocol')) is not int
                    or state['credential_import_protocol'] != 1):
                raise ValueError
        except Exception:
            raise ValueError('Selected Fleet node does not support owner credential delivery or has changed identity') from None

    async def ensure_async(self, ref, endpoint, key):
        from pantheon.apps.dependency_binding_client import _drain
        if not isinstance(ref, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref):
            raise ValueError('Invalid App credential reference')
        if not isinstance(key, str) or not 0 < len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError('Invalid App credential')
        endpoint = self.validate_endpoint(endpoint)
        # Match the existing vault lookup identity, without changing the caller's
        # API prefix. Native adapters may intentionally use a base without /v1.
        expected_endpoint = credential_identity(endpoint)
        await self.check_async()
        await _drain(asyncio.create_task(self._deliver(ref, expected_endpoint, key)))

    async def _deliver(self, ref, endpoint, key):
        import base64
        import json
        import time
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            async with asyncio.timeout(60):
                challenge = await self.lifecycle._request(self.node_id, 'credential_prepare',
                    credential_ref=ref, credential_endpoint=endpoint)
                fields = {'protocol', 'owner', 'node_id', 'challenge_id', 'ref', 'endpoint', 'expires', 'public_key', 'context'}
                if (not isinstance(challenge, dict) or set(challenge) != fields
                        or type(challenge['protocol']) is not int or challenge['protocol'] != 1
                        or challenge['owner'] != self.owner or challenge['node_id'] != self.node_id
                        or challenge['ref'] != ref or challenge['endpoint'] != endpoint
                        or not isinstance(challenge['challenge_id'], str)
                        or not re.fullmatch(r'[a-f0-9]{32}', challenge['challenge_id'])
                        or type(challenge['expires']) is not int
                        or not time.time() - 30 < challenge['expires'] <= time.time() + 150):
                    raise ValueError
                for field, limit in (('context', 8192), ('public_key', 128)):
                    if not isinstance(challenge[field], str) or len(challenge[field]) > limit:
                        raise ValueError
                aad = base64.b64decode(challenge['context'], validate=True)
                if json.loads(aad) != {**challenge, 'context': ''}:
                    raise ValueError
                peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),
                    base64.b64decode(challenge['public_key'], validate=True))
                private = ec.generate_private_key(ec.SECP256R1())
                shared = private.exchange(ec.ECDH(), peer)
                derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=b'pantheon/node-credential-import/v1').derive(shared)
                nonce = os.urandom(12)
                envelope = dict(public_key=base64.b64encode(private.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode(),
                    nonce=base64.b64encode(nonce).decode(),
                    data=base64.b64encode(AESGCM(derived).encrypt(nonce, key.encode(), aad)).decode())
                result = await self.lifecycle._request(self.node_id, 'credential_ensure',
                    credential_challenge=challenge['challenge_id'], credential_envelope=envelope)
                if not isinstance(result, dict) or result != {'ok': True}:
                    raise ValueError
        except Exception:
            raise ValueError('Fleet credential delivery failed or its outcome is unknown; retry the same value, no existing credential is replaced') from None
