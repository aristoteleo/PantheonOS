"""One editable checkout per App lineage, with real local Git branches.

The public Store repository is a remote. Moving the launch branch never checks
out files: the developer's working tree and running snapshots are independent.
"""
from pathlib import Path
import json
import uuid

from pantheon.apps.store_release import git


def branch_ref(root, name):
    # Reject Git's checkout shorthand and options; accept only actual branch refs.
    if not name or name.startswith('-') or name.startswith('@'):
        raise ValueError('Provide a local Git branch name')
    git(root, 'check-ref-format', f'refs/heads/{name}')
    return f'refs/heads/{name}'


def branches(manager, root, app_id, record):
    if not record.get('branch_model'):
        return []
    result = []
    for line in git(root, 'for-each-ref', '--format=%(refname:strip=2) %(objectname)', 'refs/heads/').splitlines():
        name, commit = line.split(' ', 1)
        try:
            manifest = manager.versions._manifest(root, commit)
            if manifest['id'] != app_id:
                continue
            result.append({'name': name, 'commit': commit, 'version': manifest['version'],
                           'manifest': manifest, 'protected': name == record.get('official_branch'),
                           'upstream': 'upstream/main' if name == record.get('official_branch') and record.get('upstream') else ''})
        except ValueError:
            continue
    return sorted(result, key=lambda b: (not b['protected'], b['name']))


def protect(app, root):
    record = app.get('install') or {}
    if record.get('official_branch') == git(root, 'branch', '--show-current').strip():
        raise ValueError('The official branch tracks upstream. Create or switch to a development branch before editing')


def adopt(manager, app):
    """Adopt an existing checkout in place; never replace its files or history."""
    record = dict(app.get('install') or {})
    record.setdefault('repository_id', app['repository_id'])
    if record.get('branch_model'):
        return record
    root = Path(app['dir'])
    current = git(root, 'branch', '--show-current').strip()
    base = record.get('parent_commit') or record.get('installed_commit') or git(root, 'rev-parse', 'HEAD').strip()
    has_upstream = bool(record.get('upstream') or record.get('published') or record.get('parent_commit') or record.get('origin') in ('bundled', 'store', 'builtin'))
    if has_upstream and not git(root, 'branch', '--list', 'official').strip():
        git(root, 'branch', 'official', base)
    record.update(branch_model=1, official_branch='official' if has_upstream else '', work_branch=current if current != 'official' and record.get('origin') in ('fork', 'builtin', 'workspace') else '', label=None)
    if record.get('origin') == 'store':
        record['upstream'] = record.pop('published', None) or record.get('upstream')
        # Public and local identity are separate: publishing personal work must
        # never attempt to write into the maintainer's public repository.
        previous_id = record['repository_id']
        record['repository_id'] = str(uuid.uuid4())
        record['origin'] = 'local'
        alias = manager.records / 'repository-aliases' / f'{previous_id}.json'
        alias.parent.mkdir(parents=True, exist_ok=True)
        alias.write_text(json.dumps({'app_id': app['id'], 'scope': app['scope'], 'repository_id': record['repository_id'], 'branch': 'official'}))
        selected = manager.versions.default(app['id'])
        if selected and selected.get('repository_id') == previous_id:
            manager.versions._default_path(app['id']).write_text(json.dumps({**selected, 'repository_id': record['repository_id'], 'branch': 'official'}))
    elif record.get('origin') == 'bundled':
        record['work_branch'] = ''
    manager._save(app['record_key'], record, app['scope'])
    return record


def migrate(manager):
    """Consolidate old installation + its known fork, preserving all old refs.

    Migration is local and does no network I/O during Store loading. An old
    checkout with dirty files is retained until its changes can be reconciled.
    Public main is connected by the explicit fetch/pull action.
    """
    warnings = []
    with manager.lock():
        # Finish a journaled move if a process stopped between rename and record save.
        aliases = manager.records / 'repository-aliases'
        for path in aliases.glob('*.json') if aliases.is_dir() else []:
            item = json.loads(path.read_text())
            if item.get('record') and not Path(item['previous_dir']).exists() and Path(item['archive']).is_dir():
                existing = manager._record(item['record_key'], item['scope'])
                if not existing.get('branch_model'):
                    manager._save(item['record_key'], item['record'], item['scope'])
        apps = manager.inventory(with_git=False, with_defaults=False)['apps']
        for child in apps:
            record = child.get('install') or {}
            if child['scope'] != 'fork' or record.get('branch_model'):
                continue
            parent = next((a for a in apps if a['id'] == child['id'] and a['repository_id'] == record.get('parent_repository_id') and
                           a['scope'] in ('user', 'workspace')), None)
            if not parent:
                continue
            root, old = Path(child['dir']), Path(parent['dir'])
            if root.is_symlink() or old.is_symlink() or not (root / '.git').is_dir() or not (old / '.git').is_dir():
                continue
            if git(old, 'status', '--porcelain').strip():
                warnings.append(f"{child['id']}: previous installation has uncommitted changes; kept both checkouts")
                continue
            archive = manager.records / 'legacy-repositories' / parent['repository_id']
            if archive.exists():
                continue
            before = git(root, 'rev-parse', 'HEAD').strip()
            # Import refs into a non-conflicting namespace before moving anything.
            prefix = f"refs/pantheon/previous/{parent['repository_id']}"
            git(root, 'fetch', '-q', '--no-tags', str(old), f'refs/heads/*:{prefix}/heads/*', f'refs/tags/*:{prefix}/tags/*')
            parent_head = git(old, 'rev-parse', 'HEAD').strip()
            if not git(root, 'branch', '--list', 'official').strip():
                git(root, 'branch', 'official', parent_head)
            record = {**record, 'branch_model': 1, 'official_branch': 'official',
                      'work_branch': git(root, 'branch', '--show-current').strip(), 'label': None,
                      'upstream': record.get('upstream') or (parent.get('install') or {}).get('published'),
                      'legacy_base': record.get('parent_commit') or parent_head,
                      'previous_repository': { 'id': parent['repository_id'], 'scope': parent['scope'], 'directory': str(archive)}}
            if not record.get('upstream') and (parent.get('install') or {}).get('origin') == 'bundled':
                catalog = json.loads((Path(__file__).resolve().parents[2] / 'pantheon/apps/official-store.json').read_text())
                published = next((item for item in catalog['apps'] if item['id'] == child['id']), None)
                if published:
                    record['upstream'] = {'id': published['repository_id'], 'app_id': child['id'], 'name': published['name'],
                                          'visibility': 'public', 'official': True, 'commit': parent_head, 'version': published['version']}
            # The clone's redundant original branch is backed up, not discarded.
            for name in ('master', 'main'):
                ref = f'refs/heads/{name}'
                if name != record['work_branch'] and git(root, 'branch', '--list', name).strip() and git(root, 'rev-parse', ref).strip() == parent_head:
                    git(root, 'update-ref', f'{prefix}/heads/{name}', parent_head)
                    git(root, 'update-ref', '-d', ref, parent_head)
            assert git(root, 'rev-parse', 'HEAD').strip() == before
            archive.parent.mkdir(parents=True, exist_ok=True)
            # Durable recovery journal precedes moving the checkout. No deletion.
            journal = manager.records / 'repository-aliases' / f"{parent['repository_id']}.json"
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(json.dumps({'app_id': child['id'], 'scope': child['scope'], 'repository_id': child['repository_id'],
                                          'branch': 'official', 'previous_dir': str(old), 'archive': str(archive),
                                          'record_key': child['record_key'], 'record': record}))
            old.rename(archive)
            try:
                manager._save(child['record_key'], record, child['scope'])
            except Exception:
                archive.rename(old)
                journal.unlink(missing_ok=True)
                raise
            selected = manager.versions.default(child['id'])
            if selected and selected.get('repository_id') == parent['repository_id']:
                manager.versions._default_path(child['id']).write_text(json.dumps({**selected, 'scope': child['scope'],
                    'repository_id': child['repository_id'], 'branch': 'official'}))
    return warnings


def create_branch(manager, app, version='', name=''):
    root = Path(app['dir'])
    record = adopt(manager, app)
    branch = name or record.get('work_branch') or 'my-work'
    ref = branch_ref(root, branch)
    if branch == record.get('official_branch'):
        raise ValueError('Choose a development branch name; official is reserved for upstream')
    exists = bool(git(root, 'branch', '--list', branch).strip())
    current = git(root, 'branch', '--show-current').strip()
    if exists and name:
        raise ValueError('Branch already exists; switch to it in Develop')
    if not exists:
        base = version or 'HEAD'
        if version.startswith('branch:'):
            base = branch_ref(root, version[7:])
        elif version and version != 'latest':
            import re
            base = version if re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', version) else f'refs/tags/{version}'
        elif version == 'latest':
            base = 'HEAD'
        commit = git(root, 'rev-parse', '--verify', '--end-of-options', f'{base}^{{commit}}').strip()
        if manager.versions._manifest(root, commit)['id'] != app['id']:
            raise ValueError('Branch belongs to another App')
        if commit != git(root, 'rev-parse', 'HEAD').strip() and git(root, 'status', '--porcelain').strip():
            raise ValueError('Commit working changes before branching from another version')
        git(root, 'switch', '-c', branch, commit)
    elif current != branch:
        if git(root, 'status', '--porcelain').strip():
            raise ValueError('Commit working changes before switching branches')
        git(root, 'switch', branch)
    record['work_branch'] = branch
    manager._save(app['record_key'], record, app['scope'])
    return {'success': True, 'app_id': app['id'], 'scope': app['scope'], 'repository_id': record['repository_id'],
            'directory': str(root), 'branch': branch, 'commit': git(root, 'rev-parse', ref).strip(), 'existing': exists}


def update_official(manager, app, repository, commit, *, version=''):
    """Advance only the upstream branch, leaving personal working files alone.

    The first binding of a pre-Store installation may have an unrelated root.
    Retain that root as a migration ref. Later updates must fast-forward.
    Caller has fetched and verified the immutable commit and holds the lock.
    """
    root = Path(app['dir'])
    record = dict(app.get('install') or {})
    manifest = manager.versions._manifest(root, commit)
    if manifest['id'] != app['id'] or repository.get('app_id') != app['id']:
        raise ValueError('Upstream belongs to another App')
    name = record.get('official_branch', 'official')
    ref = branch_ref(root, name)
    before = git(root, 'rev-parse', ref).strip()
    if record.get('upstream_bound'):
        try:
            git(root, 'merge-base', '--is-ancestor', before, commit)
        except ValueError as exc:
            raise ValueError('Upstream cannot fast-forward; existing official history was retained') from exc
    current = git(root, 'branch', '--show-current').strip()
    if current == name:
        if git(root, 'status', '--porcelain').strip():
            raise ValueError('The official working tree has edits; save them on a development branch before updating')
        git(root, 'merge', '--ff-only', commit)
    elif before != commit:
        git(root, 'update-ref', f'refs/pantheon/official-before/{before}', before)
        git(root, 'update-ref', ref, commit, before)
    record.update(upstream={**repository, 'commit': commit, 'version': version or manifest['version']},
                  upstream_bound=True, branch_model=1, official_branch=name)
    git(root, 'config', f'branch.{name}.remote', 'upstream')
    git(root, 'config', f'branch.{name}.merge', 'refs/heads/main')
    manager._save(app['record_key'], record, app['scope'])
    return {'success': True, 'app_id': app['id'], 'repository_id': app['repository_id'], 'scope': app['scope'],
            'previous_commit': before, 'commit': commit, 'branch': name,
            'message': f'Updated {name}. Development branches and open windows keep their versions.'}


def merge_branch(manager, app, root, branch):
    """Merge upstream normally, with an explicit bridge for pre-Store history."""
    record = app.get('install') or {}
    target = branch_ref(root, branch)
    if branch == record.get('official_branch') and record.get('legacy_base'):
        try:
            git(root, 'merge-base', 'HEAD', target)
        except ValueError:
            base = record['legacy_base']
            git(root, 'merge-base', '--is-ancestor', base, 'HEAD')
            remote = git(root, 'rev-parse', target).strip()
            tree = git(root, 'rev-parse', f'{target}^{{tree}}').strip()
            # Both histories are parents; the upstream tree is unchanged. This
            # gives normal three-way merge its known historical local base.
            target = git(root, '-c', 'user.name=Pantheon', '-c', 'user.email=apps@pantheon',
                         'commit-tree', tree, '-p', base, '-p', remote, '-m', 'Connect legacy App base to published upstream history').strip()
    git(root, '-c', 'user.name=Pantheon', '-c', 'user.email=apps@pantheon', 'merge', '--no-edit', target)
