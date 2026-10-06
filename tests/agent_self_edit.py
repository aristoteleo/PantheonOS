"""Self-edit acceptance helper: only the running Agent's Shell edits the copy.

The model supplies a deterministic tool command; actual copying, code changes,
Git commits and tags happen in the provider process. No installed App is edited.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil

from pantheon.apps.store_release import git, prepare_release, unpack_release


EDIT = '''
import hashlib, json, shutil, subprocess
from pathlib import Path
root = Path.cwd()
source, candidate = root/'agent-source', root/'agent-candidate'
shutil.copytree(source, candidate)
def git(*args):
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=candidate,
                   check=True, stdout=subprocess.DEVNULL)
git('init', '-q')
git('config', 'user.name', 'Agent self-edit acceptance')
git('config', 'user.email', 'agent-acceptance@example.invalid')
git('add', '.')
git('commit', '-qm', 'Original paired Agent release')
git('tag', 'v0.7.0')
backend = candidate/'backend/_vendor/pantheon/chatroom/native.py'
text = backend.read_text()
old = "'execution_protocol': 1}"
assert text.count(old) == 1
backend.write_text(text.replace(old, "'execution_protocol': 1, 'self_edit_revision': 'candidate-v1'}"))
ui = root/'ui-source'
if ui.exists():
    component = ui/'src/agent/AgentWorkspace.vue'
    text = component.read_text()
    anchor = '<main class="pane conversation">'
    assert text.count(anchor) == 1
    component.write_text(text.replace(anchor, anchor+'<p role="note" class="candidate-release-note" style="position:absolute;top:8px;left:16px;font-size:12px;pointer-events:none;z-index:1">Agent-authored frontend candidate</p>'))
for name in ('app.json', 'fleet.json'):
    path = candidate/name
    value = json.loads(path.read_text()); value['version'] = '0.7.1'
    path.write_text(json.dumps(value, indent=2)+'\\n')
shutil.rmtree(candidate/'frontend')
shutil.copytree(root/'frontend-build', candidate/'frontend')
with (candidate/'frontend/agent.css').open('a') as stream:
    stream.write('\\n:root { --agent-self-edit-revision: candidate-v1; }\\n')
package = candidate/'backend/_vendor/pantheon/__init__.py'
package.write_text(package.read_text().replace("'0.7.0'", "'0.7.1'"))
inventory = json.loads((candidate/'release.json').read_text())
inventory['version'] = '0.7.1'
inventory['files'] = {p.relative_to(candidate).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(candidate.rglob('*'))
    if p.is_file() and '.git' not in p.relative_to(candidate).parts and p != candidate/'release.json'}
(candidate/'release.json').write_text(json.dumps(inventory, indent=2)+'\\n')
if not ui.exists():
    git('add', '.')
    git('commit', '-qm', 'Edit Agent backend and paired frontend working copy')
    git('tag', 'v0.7.1')
print('AGENT_SELF_EDIT_COMPLETE')
'''

PAIR = '''
import hashlib, json, shutil, subprocess
from pathlib import Path
root = Path.cwd(); candidate = root/'agent-candidate'
report = json.loads((root/'compiled-ui/build-report.json').read_text())
assert report['version'] == '0.7.1' and report['desktopImplementation'] is False
shutil.rmtree(candidate/'frontend')
shutil.copytree(root/'compiled-ui', candidate/'frontend')
shutil.copytree(root/'ui-source', candidate/'frontend-source')
inventory = json.loads((candidate/'release.json').read_text())
inventory['files'] = {p.relative_to(candidate).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(candidate.rglob('*'))
    if p.is_file() and '.git' not in p.relative_to(candidate).parts and p != candidate/'release.json'}
(candidate/'release.json').write_text(json.dumps(inventory, indent=2)+'\\n')
for args in [('add','.'), ('commit','-qm','Pair Agent-authored Vue source and backend with validated build'), ('tag','v0.7.1')]:
    subprocess.run(['git','-c','core.hooksPath=/dev/null',*args],cwd=candidate,check=True,stdout=subprocess.DEVNULL)
print('AGENT_FRONTEND_PAIRED')
'''


async def edit_candidate(workspace, source, frontend, model_endpoint, invoke, chat_id, *, ui_source=None):
    workspace = Path(workspace)
    before = (source/'release.json').read_bytes()
    # Export code only, matching the package builder/Fleet artifact exclusions.
    # A prior interpreter probe may have populated bytecode beside the source.
    shutil.copytree(source, workspace/'agent-source',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(frontend, workspace/'frontend-build')
    if ui_source is not None:
        ui_source = Path(ui_source)
        ui = workspace/'ui-source'
        ui.mkdir()
        shutil.copytree(ui_source/'src', ui/'src')
        for name in ('package.json', 'pnpm-lock.yaml', 'vite.agentapp.config.ts'):
            shutil.copyfile(ui_source/name, ui/name)
    model_endpoint.tool_command = 'python3 -c ' + shlex.quote(EDIT)
    try:
        result = await invoke('chat', chat_id=chat_id, message=[{
            'role': 'user', 'content': 'Edit the exported Agent App in a new working copy; create a candidate release.'}])
        assert result['success']
    finally:
        model_endpoint.tool_command = 'printf PROFILE_TOOL_OK'
    if ui_source is not None:
        # The author edits source through Shell; the owner runs the normal build
        # as candidate validation. Reuse only local build dependencies, never
        # include node_modules or a machine-specific link in the published App.
        link = ui/'node_modules'
        link.symlink_to(ui_source/'node_modules', target_is_directory=True)
        try:
            with (workspace/'frontend-build.log').open('wb') as log:
                child = await asyncio.create_subprocess_exec('node', str(link/'vite/bin/vite.js'),
                    'build', '--config', 'vite.agentapp.config.ts', cwd=ui,
                    env={**os.environ, 'AGENT_APP_VERSION':'0.7.1',
                         'AGENT_APP_REVISION':'agent-authored-candidate',
                         'AGENT_APP_BUILD_DIR':str(workspace/'compiled-ui')}, stdout=log, stderr=log)
                try:
                    async with asyncio.timeout(180): assert await child.wait() == 0, 'See frontend-build.log'
                finally:
                    if child.returncode is None: child.kill(); await child.wait()
        finally:
            link.unlink()
        model_endpoint.tool_command = 'python3 -c ' + shlex.quote(PAIR)
        try:
            assert (await invoke('chat', chat_id=chat_id, message=[{'role':'user',
                'content':'Pair the validated frontend build with your backend edit and commit the candidate.'}]))['success']
        finally:
            model_endpoint.tool_command = 'printf PROFILE_TOOL_OK'
    candidate = workspace/'agent-candidate'
    assert (source/'release.json').read_bytes() == before
    assert 'self_edit_revision' not in (source/'backend/_vendor/pantheon/chatroom/native.py').read_text()
    assert git(candidate, 'status', '--porcelain').strip() == ''
    assert git(candidate, 'rev-list', '--count', 'HEAD').strip() == '2'
    assert git(candidate, 'rev-parse', 'v0.7.0').strip() != git(candidate, 'rev-parse', 'v0.7.1').strip()
    # Ordinary Store export/import, not a test-only package codec. Keep only
    # the public tag's bytes when handing the candidate to Fleet.
    release = prepare_release(candidate)
    imported = workspace/'reviewed-agent'
    unpack_release(release['app_release'], imported, '0.7.1')
    inventory = json.loads((imported/'release.json').read_text())
    assert inventory['version'] == '0.7.1'
    assert all(hashlib.sha256((imported/name).read_bytes()).hexdigest() == digest
               for name, digest in inventory['files'].items())
    if ui_source is None:
        assert '--agent-self-edit-revision: candidate-v1' in (imported/'frontend/agent.css').read_text()
    else:
        assert 'Agent-authored frontend candidate' in (imported/'frontend-source/src/agent/AgentWorkspace.vue').read_text()
        assert not (imported/'frontend-source/node_modules').exists()
    return imported


async def render_candidate(root, package, invoke, chat_id, *, candidate):
    from pantheon.apps.lifecycle import build_artifact
    from pantheon.apps.owner_journal import OwnerJournal
    from pantheon.platform.local_desktop import DesktopView, snapshot_frontend
    script = os.environ['AGENT_SELF_EDIT_UI_TEST']
    root.mkdir()
    assets = root/'assets'; assets.mkdir()
    _, revision = await asyncio.to_thread(build_artifact, package)
    await asyncio.to_thread(snapshot_frontend, {'path':str(package), 'revision':revision}, assets)
    state = root/'view.json'
    await OwnerJournal(root)._checkpoint(state, {'chatId':chat_id})
    async def call(method, args, timeout): return await invoke(method, **args)
    async with DesktopView(assets, call, state) as view:
        child = await asyncio.create_subprocess_exec('node', script,
            env={**os.environ, 'AGENT_SELF_EDIT_VIEW':view.url,
                 'AGENT_SELF_EDIT_EXPECTED':'candidate' if candidate else 'source',
                 'AGENT_SELF_EDIT_SCREENSHOT':str(root/'rendered.png')},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(100): out, err = await child.communicate()
            assert child.returncode == 0, out.decode()+err.decode()
        finally:
            if child.returncode is None: child.kill(); await child.wait()
