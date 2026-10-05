"""Sealed ordinary App image delivery with an explicit SDK build fixture."""
import asyncio
import io
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.modal_image import build_modal_image
from pantheon.evolution.sandbox.package import build_package


class SDK:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()
        self.calls = []
        self.Image = SimpleNamespace(from_id=self.image)
        async def app(name, **kwargs):
            return name
        self.App = SimpleNamespace(lookup=SimpleNamespace(aio=app))

    def image(self, identity):
        sdk = self
        self.calls.append(('base', identity))
        class Image:
            object_id = 'im-built'
            def pip_install_from_requirements(self, path, **kwargs):
                self.requirements = Path(path)
                sdk.calls.append(('requirements', self.requirements.read_bytes(), kwargs))
                return self
            def add_local_file(self, path, destination, **kwargs):
                self.artifact = Path(path)
                sdk.calls.append(('artifact', self.artifact.read_bytes(), destination, kwargs))
                return self
            def run_commands(self, command):
                sdk.calls.append(('command', command))
                return self
            async def compile(self, app):
                sdk.entered.set()
                await sdk.release.wait()
                # Cancellation must not remove input files while the SDK owns them.
                assert self.artifact.exists() and self.requirements.exists()
        image = Image()
        image.build = SimpleNamespace(aio=image.compile)
        return image


@pytest.mark.asyncio
async def test_build_uses_exact_artifact_and_no_configuration(tmp_path, monkeypatch):
    package = build_package(tmp_path / 'app')
    (package / '.env').write_text('SECRET=private')
    payload, digest = build_artifact(package)
    sdk = SDK()
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-copy')
    result = await build_modal_image(package, artifact_sha256=digest,
        base_image_id='im-base', app_name='test', modal_sdk=sdk)
    assert result.artifact_sha256 == digest and result.image_id == 'im-built'
    assert sdk.calls[0] == ('base', 'im-base')
    assert sdk.calls[1][2] == {'extra_options': '--require-hashes --only-binary=:all:', 'secrets': []}
    assert sdk.calls[2][1] == payload and sdk.calls[2][3] == {'copy': True}
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        assert '.env' not in tar.getnames()
        assert tar.extractfile('requirements.txt').read() == sdk.calls[1][1]
    assert 'must-not-copy' not in str(sdk.calls) and 'SECRET=private' not in str(sdk.calls)
    assert digest in sdk.calls[3][1]
    assert result.argv()[1] == '-u' and 'evolution-tools' in result.argv()


@pytest.mark.asyncio
async def test_changed_artifact_is_rejected_before_remote_build(tmp_path):
    package = build_package(tmp_path / 'app')
    _, digest = build_artifact(package)
    (package / 'backend/__init__.py').write_text('changed')
    sdk = SDK()
    with pytest.raises(ValueError, match='changed after review'):
        await build_modal_image(package, artifact_sha256=digest,
            base_image_id='im-base', app_name='test', modal_sdk=sdk)
    assert sdk.calls == []


@pytest.mark.asyncio
async def test_cancel_build_joins_sdk_before_removing_local_inputs(tmp_path):
    package = build_package(tmp_path / 'app')
    _, digest = build_artifact(package)
    sdk = SDK()
    sdk.release.clear()
    task = asyncio.create_task(build_modal_image(package, artifact_sha256=digest,
        base_image_id='im-base', app_name='test', modal_sdk=sdk))
    await sdk.entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    sdk.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
