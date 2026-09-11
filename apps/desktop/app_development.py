"""Repository-scoped development on the node that owns the App sources."""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from pantheon.apps.store_release import git, validate_manifest


def source_path(root: Path, path: str) -> Path:
    relative = Path(path)
    if not path or relative.is_absolute() or any(p in ('.git', '..') for p in relative.parts):
        raise ValueError('Use a relative App source path outside .git')
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Source path escapes the App repository')
    return target


def develop(manager, action: str, app_id: str, scope: str, repository_id: str,
            path='', files=None, branch='', message='', expected_commit='', command=None) -> dict:
    if action == 'create':
        manager.versions._default_path(app_id)
        with manager.lock():
            if any(a['id'] == app_id for a in manager.inventory()['apps']):
                raise ValueError('App already exists; fork its repository instead')
            root = manager.user_root / app_id
            root.mkdir(parents=True, exist_ok=False)
            manifest = {'id': app_id, 'name': message or app_id, 'version': '0.1.0',
                        'apiVersion': 2, 'surface': 'dom', 'entry': {'frontend': 'index.js'}}
            (root / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
            (root / 'index.js').write_text('export function setup(app, root) {\n  root.textContent = "Your new App"\n}\n')
            (root / '.gitignore').write_text('node_modules/\n.venv/\n__pycache__/\n*.pyc\n.env\n.env.*\n')
            git(root, 'init', '-q', '-b', 'main')
            git(root, 'add', '-A')
            git(root, '-c', 'user.name=Pantheon', '-c', 'user.email=apps@pantheon', 'commit', '-qm', 'Create App')
            repo_id = str(uuid.uuid4())
            manager._save(app_id, {'repository_id': repo_id, 'origin': 'local', 'branch_model': 1, 'work_branch': 'main'})
        return {'success': True, 'app': manager.find(app_id, 'user', repo_id)}
    app = manager.find(app_id, scope or None, repository_id)
    root = Path(app['git'].get('path') or app['dir'])
    if action == 'status':
        return {'success': True, 'app': app}
    if action in ('read', 'files') and branch and branch != app['git'].get('branch'):
        from .local_repository import branch_ref
        ref = branch_ref(root, branch)
        if action == 'files':
            return {'success': True, 'directory': str(root), 'files': git(root, 'ls-tree', '-r', '--name-only', ref).splitlines()}
        source_path(root, path)
        content = git(root, 'show', f'{ref}:{path}')
        if len(content.encode()) > 1024 * 1024:
            raise ValueError('Read smaller source files (limit 1 MiB)')
        return {'success': True, 'path': path, 'content': content}
    if action == 'read':
        target = source_path(root, path)
        if target.stat().st_size > 1024 * 1024:
            raise ValueError('Read smaller source files (limit 1 MiB)')
        return {'success': True, 'path': path, 'content': target.read_text(), 'commit': app['git']['commit']}
    if action == 'files':
        return {'success': True, 'directory': str(root), 'files': sorted(set(
            git(root, 'ls-files', '--cached', '--others', '--exclude-standard').splitlines()))}
    if action == 'diff':
        return {'success': True, 'diff': git(root, 'diff', 'HEAD', '--')[:100000], 'changes': app['git'].get('changes', [])}
    if scope == 'builtin' or app['scope'] not in ('user', 'fork', 'workspace') or (app.get('install') or {}).get('origin') == 'store':
        raise ValueError('Fork this App into a private repository before editing it')
    if (app.get('install') or {}).get('branch_model') and action not in ('branch', 'switch', 'test'):
        from .local_repository import protect
        protect(app, root)
    if action == 'test':
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError('Provide the test command as an argument array, e.g. ["python", "-m", "pytest"]')
        # Executes in the source repository, never in an immutable running snapshot.
        with __import__('tempfile').TemporaryFile() as output:
            process = subprocess.Popen(command, cwd=root, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                import os, signal
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ValueError('Test exceeded 120 seconds')
            output.seek(0)
            return {'success': code == 0, 'exit_code': code, 'output': output.read(100000).decode(errors='replace')}
    with manager.lock():
        if expected_commit and git(root, 'rev-parse', 'HEAD').strip() != expected_commit:
            raise ValueError('Repository changed; read its status before retrying')
        if action == 'write':
            if not isinstance(files, dict) or not files or len(files) > 100:
                raise ValueError('Provide 1–100 source files')
            targets = [(source_path(root, name), content) for name, content in files.items()]
            if any(not isinstance(content, str) for _, content in targets) or sum(len(content.encode()) for _, content in targets) > 2 * 1024 * 1024:
                raise ValueError('Source batch exceeds 2 MiB')
            for target, content in targets:
                if target.name in ('app.json', 'atrium.json') and target.parent == root:
                    if validate_manifest(json.loads(content))['id'] != app_id:
                        raise ValueError('Editing cannot change the App identity')
            for target, content in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        elif action in ('branch', 'switch', 'merge'):
            from .local_repository import branch_ref
            branch_ref(root, branch)
            if action == 'branch' and branch == (app.get('install') or {}).get('official_branch'):
                raise ValueError('official is reserved for upstream')
            if git(root, 'status', '--porcelain').strip():
                raise ValueError('Commit working changes before switching or merging branches')
            if action == 'branch':
                git(root, 'switch', '-c', branch)
            elif action == 'switch':
                git(root, 'switch', branch)
            else:
                from .local_repository import merge_branch
                merge_branch(manager, app, root, branch)
            if action in ('branch', 'switch') and (app.get('install') or {}).get('branch_model') and branch != app['install'].get('official_branch'):
                manager._save(app['record_key'], {**app['install'], 'work_branch': branch}, app['scope'])
        elif action == 'commit':
            if not message.strip():
                raise ValueError('Provide a commit message')
            git(root, 'add', '-A')
            git(root, '-c', 'user.name=Pantheon', '-c', 'user.email=apps@pantheon', 'commit', '-qm', message)
        else:
            raise ValueError('Actions: create, status, files, read, write, diff, branch, switch, merge, commit, test')
    return {'success': True, 'app': manager.find(app_id, app['scope'], app['repository_id'])}
