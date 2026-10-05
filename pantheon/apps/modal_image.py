"""Prepare a pinned Modal image from an existing ordinary Python App artifact.

This uses the normal Fleet package digest, not a parallel App version system.
Only the sealed artifact enters the build; source checkout paths, controller
environment and mutable configuration do not. Installation occurs during image
preparation, never on each container start.
"""
import asyncio
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import shlex
import tarfile
import tempfile

from .modal_sandbox import _join
from pantheon.utils.owned_io import run_owned_io


@dataclass(frozen=True)
class ModalAppImage:
    app_id: str
    version: str
    artifact_sha256: str
    image_id: str
    base_image_id: str

    def argv(self):
        return ['python', '-u', '/opt/pantheon-app/.fleet-runtime/app_runtime.py',
                '--app-dir', '/opt/pantheon-app', '--app-id', self.app_id,
                '--workspace', '/workspace', '--state-dir', '/state']


async def build_modal_image(package, *, artifact_sha256, base_image_id, app_name, modal_sdk=None):
    """Build exactly the reviewed Linux/amd64 artifact on a pinned base image.

    The base must already provide Python 3.12 and pip. Hash-locked Python
    dependencies come from that artifact. An owner may prepare a richer pinned
    base for task-specific system libraries without changing the App protocol.
    Cancellation joins the actual build before deleting its local input files.
    """
    if not isinstance(artifact_sha256, str) or not re.fullmatch('[a-f0-9]{64}', artifact_sha256):
        raise ValueError('Supply the reviewed ordinary App artifact digest')
    if not isinstance(base_image_id, str) or not re.fullmatch('im-[A-Za-z0-9]+', base_image_id):
        raise ValueError('Supply an immutable prepared base image ID')
    if not isinstance(app_name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', app_name):
        raise ValueError('Supply a Modal App name')
    from .lifecycle import build_artifact
    payload, digest = await run_owned_io(build_artifact, Path(package), 'linux-amd64')
    if digest != artifact_sha256:
        raise ValueError('App artifact changed after review')
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:*') as archive:
        manifest = json.load(archive.extractfile('app.json'))
        definition = json.load(archive.extractfile('fleet.json'))
        required = definition.get('requires', {})
        if 'linux' not in required.get('os', []) or 'amd64' not in required.get('arch', []):
            raise ValueError('This App artifact does not support Linux/amd64')
        names = set(archive.getnames())
        entry = manifest.get('entry', {}).get('backend', '')
        if not entry or entry not in names or '.fleet-runtime/app_runtime.py' not in names:
            raise ValueError('Supply a prepared ordinary Python App with its stdio host')
        requirements = archive.extractfile('requirements.txt').read()
        if b'--hash=sha256:' not in requirements:
            raise ValueError('App dependencies must have a hash-locked requirements file')
    sdk = modal_sdk
    if sdk is None:
        import modal
        sdk = modal
    async def build():
        app = await sdk.App.lookup.aio(app_name, create_if_missing=True)
        with tempfile.TemporaryDirectory(prefix='modal-app-image-') as temporary:
            directory = Path(temporary)
            (directory / 'app.tar').write_bytes(payload)
            (directory / 'requirements.txt').write_bytes(requirements)
            image = sdk.Image.from_id(base_image_id)
            image = image.pip_install_from_requirements(str(directory / 'requirements.txt'),
                extra_options='--require-hashes --only-binary=:all:', secrets=[])
            image = image.add_local_file(directory / 'app.tar', '/opt/pantheon-app.tar', copy=True)
            script = (
                'import hashlib,pathlib,tarfile; p=pathlib.Path("/opt/pantheon-app.tar"); '
                f'assert hashlib.sha256(p.read_bytes()).hexdigest()=={digest!r}; '
                't=tarfile.open(p); t.extractall("/opt/pantheon-app",filter="data"); '
                't.close(); p.unlink(); '
                '[pathlib.Path(p).mkdir(exist_ok=True) for p in ("/workspace","/state")]; '
                f'pathlib.Path("/opt/pantheon-app.sha256").write_text({digest!r})')
            image = image.run_commands('python -c ' + shlex.quote(script))
            await image.build.aio(app)
            return ModalAppImage(manifest['id'], manifest['version'], digest, image.object_id, base_image_id)
    return await _join(asyncio.create_task(build()))
