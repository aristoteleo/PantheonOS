"""Normal Git fetch/pull for an installed repository and its upstream."""
import json
from pathlib import Path

from pantheon.apps.store_release import git, validate_manifest


def sync_upstream(manager, app, repository, clone_url, *, pull=False, expected_commit=''):
    root = Path(app['dir'])
    installed = (app.get('install') or {}).get('origin') == 'store'
    remote = 'origin' if installed else 'upstream'
    target = f'refs/remotes/{remote}/main'
    with manager.lock():
        unified = bool((app.get('install') or {}).get('branch_model'))
        if pull and unified and (not expected_commit or expected_commit != repository['head']):
            raise ValueError('The remote main changed. Refresh and review the update before pulling')
        if pull and not unified:
            if not expected_commit or expected_commit != repository['head']:
                raise ValueError('The remote main changed. Refresh and review the update before pulling')
            if git(root, 'status', '--porcelain').strip():
                raise ValueError('Commit or discard your working-tree changes before pulling; no files were overwritten')
            branch = git(root, 'branch', '--show-current').strip()
            if not branch:
                raise ValueError('Check out a branch before pulling; this checkout is pinned to a commit')
            if installed and branch != 'main':
                raise ValueError('Check out main before updating this installation; your current branch is unchanged')
        remotes = git(root, 'remote').splitlines()
        git(root, 'remote', 'set-url' if remote in remotes else 'add', remote, clone_url)
        git(root, 'fetch', '--no-tags', remote, f'+refs/heads/main:{target}',
            f'+refs/tags/*:refs/remotes/{remote}/tags/*')
        commit = git(root, 'rev-parse', target).strip()
        if pull:
            if commit != expected_commit:
                raise ValueError('The remote main changed during fetch. Refresh before pulling; local files are unchanged')
            manifest = validate_manifest(json.loads(git(root, 'show', f'{target}:app.json')))
            if manifest['id'] != app['id']:
                raise ValueError('Upstream main changed App identity')
            if unified:
                from .local_repository import update_official
                return update_official(manager, app, repository, commit)
            tags = []
            prefix = f'refs/remotes/{remote}/tags/'
            for line in git(root, 'for-each-ref', '--format=%(refname)', prefix).splitlines():
                tag = line[len(prefix):]
                value = git(root, 'rev-parse', f'{line}^{{commit}}').strip()
                if git(root, 'tag', '--list', tag).strip():
                    if git(root, 'rev-parse', f'refs/tags/{tag}^{{commit}}').strip() != value:
                        # Fork version names may overlap upstream. Keep both
                        # identities visible via the namespaced remote refs.
                        continue
                else:
                    tags.append((tag, value))
            before = git(root, 'rev-parse', 'HEAD').strip()
            try:
                if installed:
                    # Official/community installations never discard local commits.
                    git(root, 'merge', '--ff-only', target)
                else:
                    git(root, '-c', 'user.name=Pantheon', '-c', 'user.email=apps@pantheon',
                        'merge', '--no-edit', target)
            except ValueError as exc:
                conflicts = git(root, 'diff', '--name-only', '--diff-filter=U').splitlines()
                if conflicts:
                    # Report real conflict paths while restoring the pre-pull tree.
                    git(root, 'merge', '--abort')
                    raise ValueError('Merge conflicts in ' + ', '.join(conflicts) + '. Pull was aborted; resolve the upstream changes in Develop or Terminal') from exc
                raise ValueError('Cannot fast-forward or merge this history. Keep your commits and merge upstream in Develop or Terminal') from exc
            record = dict(app.get('install') or {})
            record['upstream'] = {**repository, 'commit': commit}
            if installed:
                record.update(published=repository, version=manifest['version'], installed_commit=commit)
                git(root, 'config', 'branch.main.remote', remote)
                git(root, 'config', 'branch.main.merge', 'refs/heads/main')
                # Local tags remain immutable. Remote tag refs are separately visible.
            manager._save(app['record_key'], record, app['scope'])
            for tag, value in tags:
                git(root, 'tag', tag, value)
            return {'success': True, 'previous_commit': before, 'commit': git(root, 'rev-parse', 'HEAD').strip(),
                    'message': 'Pulled remote main. Existing version snapshots and open windows are unchanged.'}
        return {'success': True, 'commit': commit, 'message': 'Fetched remote main. Working files are unchanged.'}
