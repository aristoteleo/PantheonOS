"""Prepared Files distribution reuses filesystem code without importing Agent."""
import json
import asyncio
import base64
import io
from pathlib import Path
import subprocess
import sys

import pytest

from pantheon.apps.builtin.file.build_managed import build
from pantheon.apps.builtin.file.managed import METHODS, create_service
from pantheon.apps.lifecycle import build_artifact
from PIL import Image


def test_package_declares_exact_rpc_surface_and_loads_without_agent(tmp_path):
    package = build(tmp_path/'files', 'darwin-arm64')
    manifest = json.loads((package/'app.json').read_text())
    assert {t['name'] for t in manifest['provides']['tools']} == METHODS
    assert all(set(i['tools']) <= METHODS for i in manifest['provides']['interfaces'])
    assert not list(package.rglob('agent.py')) and not list(package.rglob('settings.py'))
    build_artifact(package)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    Image.new('RGB', (1200, 400), 'red').save(workspace/'preview.png')
    code = '''import asyncio, importlib.abc, json, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name in ('pantheon.agent','pantheon.settings','pantheon.factory','pantheon.remote','pantheon.chatroom'):
   raise AssertionError('Files imported Agent or ambient state: '+name)
sys.meta_path.insert(0, Boundary())
sys.path.insert(0,sys.argv[1])
from pantheon.apps.builtin.file.managed import create_service
async def run():
 service=create_service({'workspace':sys.argv[2], 'limits':{'max_file_read_chars':5}})
 assert (await service.write_file('sample.py', 'def sample():\\n    return 42\\n'))['success']
 assert (await service.read_file('sample.py'))['content']=='def s'
 assert (await service.update_file('sample.py','42','43'))['success']
 assert (await service.glob('*.py'))['success']
 assert (await service.grep('return',path='sample.py'))['success']
 assert (await service.view_file_outline('sample.py'))['success']
 assert not (await service.read_file('.pantheon/agents/missing.md'))['success']
 preview = await service.fetch_image_base64('preview.png', 120)
 assert preview['success'] and preview['data_uri'].startswith('data:image/jpeg;base64,')
 await service.cleanup()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable,'-I','-c',code,str(package/'backend/_vendor'),str(workspace)],
                            cwd=tmp_path,text=True,capture_output=True,timeout=30)
    assert result.returncode == 0, result.stderr
    assert '43' in (workspace/'sample.py').read_text()


@pytest.mark.asyncio
async def test_preview_belongs_to_files_node_and_never_ambient_agent_store(tmp_path, monkeypatch):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    Image.new('RGBA', (1200, 600), 'red').save(workspace/'plot.png')
    Image.new('RGB', (12, 12), 'blue').save(tmp_path/'plot.png')
    (workspace/'inside.png').symlink_to(workspace/'plot.png')
    (workspace/'escape.png').symlink_to(tmp_path/'plot.png')
    def forbidden(*args, **kwargs):
        raise AssertionError('Ambient Agent state was consulted')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    service = create_service({'workspace': str(workspace)})
    try:
        for path in ('plot.png', str(workspace/'plot.png'), 'inside.png'):
            result = await service.fetch_image_base64(path, 200)
            assert result['success'], result
            with Image.open(io.BytesIO(base64.b64decode(result['data_uri'].split(',', 1)[1]))) as image:
                assert image.size == (200, 100)
                assert image.getpixel((0, 0)) == (255, 0, 0, 255)
        for path in ('../plot.png', str(tmp_path/'plot.png'), 'escape.png', 'missing.png'):
            assert not (await service.fetch_image_base64(path))['success']
        for size in (True, 0, -1, 4097, '200'):
            assert not (await service.fetch_image_base64('plot.png', size))['success']
    finally:
        await service.cleanup()


@pytest.mark.asyncio
async def test_preview_preserves_animation_and_bounds_bytes_and_decode_pixels(tmp_path):
    service = create_service({'workspace': str(tmp_path)})
    try:
        first, second = Image.new('RGB', (3, 2), 'red'), Image.new('RGB', (3, 2), 'blue')
        first.save(tmp_path/'animated.gif', save_all=True, append_images=[second], duration=100, loop=0)
        result = await service.fetch_image_base64('animated.gif', 1)
        assert result['success']
        assert base64.b64decode(result['data_uri'].split(',', 1)[1]) == (tmp_path/'animated.gif').read_bytes()
        (tmp_path/'large.gif').write_bytes(b'x' * (10 * 1024 * 1024 + 1))
        assert not (await service.fetch_image_base64('large.gif'))['success']
        Image.new('1', (6500, 6500)).save(tmp_path/'many-pixels.png')
        assert not (await service.fetch_image_base64('many-pixels.png'))['success']
    finally:
        await service.cleanup()


@pytest.mark.asyncio
async def test_cancelled_preview_keeps_decode_admission_until_worker_finishes(tmp_path, monkeypatch):
    import threading
    from pantheon.utils import vision
    (tmp_path/'plot.png').write_bytes(b'fixture')
    entered, release = threading.Event(), threading.Event()
    def encode(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return 'data:image/png;base64,AA=='
    monkeypatch.setattr(vision, 'get_image_base64', encode)
    service = create_service({'workspace': str(tmp_path)})
    service._preview_slots = asyncio.Semaphore(1)
    first = asyncio.create_task(service.fetch_image_base64('plot.png'))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        second = asyncio.create_task(service.fetch_image_base64('plot.png'))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(.03)
        assert not first.done() and not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second)['success']
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await service.cleanup()


@pytest.mark.parametrize('value', [None, {}, {'workspace':'relative'}, {'workspace':'/missing'},
    {'workspace':'$workspace','limits':[]},
    {'workspace':'$workspace','limits':{'max_file_read_chars':True}},
    {'workspace':'$workspace','limits':{'max_file_read_lines':0}},
    {'workspace':'$workspace','limits':{'unknown':1}}, {'workspace':'$workspace','credentials':{}}])
def test_prepared_configuration_fails_before_host_admission(tmp_path,value):
    if isinstance(value,dict) and value.get('workspace') == '$workspace':
        value = {**value, 'workspace':str(tmp_path)}
    with pytest.raises(ValueError):
        create_service(value)
