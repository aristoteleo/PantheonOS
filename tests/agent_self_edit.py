"""Self-edit acceptance helper: only the running Agent's Shell edits the copy.

The model supplies a deterministic tool command; actual copying, code changes,
Git commits and tags happen in the provider process. No installed App is edited.
"""
import hashlib
import json
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
git('add', '.')
git('commit', '-qm', 'Edit Agent backend and paired frontend working copy')
git('tag', 'v0.7.1')
print('AGENT_SELF_EDIT_COMPLETE')
'''


async def edit_candidate(workspace, source, frontend, model_endpoint, invoke, chat_id):
    workspace = Path(workspace)
    before = (source/'release.json').read_bytes()
    shutil.copytree(source, workspace/'agent-source')
    shutil.copytree(frontend, workspace/'frontend-build')
    model_endpoint.tool_command = 'python3 -c ' + shlex.quote(EDIT)
    try:
        result = await invoke('chat', chat_id=chat_id, message=[{
            'role': 'user', 'content': 'Edit the exported Agent App in a new working copy; create a candidate release.'}])
        assert result['success']
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
    assert '--agent-self-edit-revision: candidate-v1' in (imported/'frontend/agent.css').read_text()
    return imported
