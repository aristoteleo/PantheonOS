"""Agent and Store use the same public repository/release contract."""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx

from pantheon.apps.store_release import git, unpack_release


async def store_action(manager, action, app_id, repository_id, scope, version, name, query, changelog, expected_commit, *,
                       target_repository_id="", expected_base="", request_id="", title="", description="",
                       decision="", inbox="all"):
    from pantheon.store.auth import StoreAuth
    auth = StoreAuth()
    base = (os.environ.get('PANTHEON_HUB_URL') or auth.hub_url or 'https://app.pantheonos.stanford.edu').rstrip('/')
    token = os.environ.get('PANTHEON_STORE_TOKEN') or auth.token
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    async with httpx.AsyncClient(base_url=base, timeout=120) as client:
        async def request(method, path, **kwargs):
            response = await client.request(method, '/api/store/' + path, headers=headers, **kwargs)
            if response.is_error:
                try:
                    detail = response.json().get('detail', 'Store request failed')
                except ValueError:
                    detail = f'Store request failed ({response.status_code})'
                raise ValueError(str(detail))
            return response.json()
        try:
            if action == 'search':
                return await request('GET', 'packages', params={'type': 'app', 'q': query, 'limit': 20})
            if action == 'inspect':
                return await request('GET', 'packages/' + quote(repository_id, safe=''))
            if action == 'community':
                if not app_id:
                    raise ValueError('Provide app_id to find its public repositories')
                return await request('GET', f'apps/{quote(app_id, safe="")}/community')
            if action == 'contributions':
                return await request('GET', 'app-contributions', params={
                    'app_id': app_id, 'repository_id': repository_id, 'inbox': inbox})
            if action == 'submit':
                if not all((repository_id, version, target_repository_id, expected_commit, expected_base, title.strip())):
                    raise ValueError('Inspect source and upstream first; provide repository_id, version, target_repository_id, expected_commit, expected_base and title')
                return await request('POST', 'app-contributions', json={
                    'source_id': repository_id, 'source_version': version, 'target_id': target_repository_id,
                    'expected_source': expected_commit, 'expected_base': expected_base,
                    'title': title, 'description': description})
            if action in ('inspect_contribution', 'prepare_merge', 'review', 'merge', 'close_contribution', 'checkout_review'):
                if not request_id:
                    raise ValueError('Provide request_id from the contribution inbox')
                endpoint = 'app-contributions/' + quote(request_id, safe='')
                if action == 'inspect_contribution':
                    return await request('GET', endpoint)
                if action == 'checkout_review':
                    if not expected_commit:
                        raise ValueError('Inspect the candidate and provide its expected_commit')
                    result = await request('GET', endpoint + '/candidate', params={'expected_commit': expected_commit})
                    release = result['app_release']
                    if release['commit'] != expected_commit:
                        raise ValueError('Candidate changed before checkout')
                    def checkout():
                        reviews = manager.records / 'reviews'
                        reviews.mkdir(parents=True, exist_ok=True)
                        parent = Path(tempfile.mkdtemp(prefix='candidate-', dir=reviews))
                        destination = parent / 'repository'
                        try:
                            manifest = unpack_release(release, destination, result['manifest']['version'])
                            return {'success': True, 'directory': str(destination), 'commit': expected_commit,
                                    'manifest': manifest, 'request_id': request_id,
                                    'message': 'Private review checkout on this Desktop node. Inspect and run tests here before approving; no App instance or default was changed.'}
                        except Exception:
                            shutil.rmtree(parent, ignore_errors=True)
                            raise
                    return await asyncio.to_thread(checkout)
                suffix, payload = {
                    'prepare_merge': ('prepare', {'version': version}),
                    'review': ('review', {'decision': decision, 'expected_commit': expected_commit, 'comment': description}),
                    'merge': ('merge', {'expected_commit': expected_commit}),
                    'close_contribution': ('close', {}),
                }[action]
                return await request('POST', endpoint + '/' + suffix, json=payload)
            if action == 'fork':
                if not version:
                    raise ValueError('Choose a released version before forking')
                download = await request('GET', f'packages/{quote(repository_id, safe="")}/download/{quote(version, safe="")}')
                repo = download.get('repository') or {}
                if repo.get('clone_url'):
                    repo['clone_url'] = urljoin(base, repo['clone_url'])
                return await asyncio.to_thread(manager.branches.fork_download, download, name)
            app = await asyncio.to_thread(manager.find, app_id, scope, repository_id)
            if action == 'fetch':
                upstream = (app.get('install') or {}).get('upstream') or {}
                if not upstream.get('id'):
                    raise ValueError('This private repository has no Store upstream')
                package = await request('GET', 'packages/' + quote(upstream['id'], safe=''))
                clone = (package.get('repository') or {}).get('clone_url')
                if not clone:
                    raise ValueError('Upstream repository is not public')
                root = Path(app['dir'])
                # Isolated refs: a same-named local tag or branch is never replaced.
                def fetch_upstream():
                    with manager.lock():
                        git(root, 'fetch', '--no-tags', urljoin(base, clone),
                            '+refs/heads/main:refs/remotes/upstream/main', '+refs/tags/*:refs/remotes/upstream/tags/*')
                await asyncio.to_thread(fetch_upstream)
                return {'success': True, 'repository_id': app['repository_id'],
                        'message': 'Upstream fetched. Inspect the Git graph and merge a chosen upstream ref explicitly.'}
            if action != 'publish':
                raise ValueError('Actions: search, inspect, fork, fetch, publish, community, contributions, submit, inspect_contribution, prepare_merge, checkout_review, review, merge, close_contribution')
            if not token:
                raise ValueError('Sign in to Store before publishing')
            release = await asyncio.to_thread(manager.prepare, app_id, scope, repository_id)
            if not expected_commit or release['app_release']['commit'] != expected_commit:
                raise ValueError('Prepare and review the release first, then supply its expected_commit')
            published = (app.get('install') or {}).get('published')
            if not published and not name:
                raise ValueError('Choose a unique Store repository name for the first release')
            manifest = release['manifest']
            data = {'name': name, 'type': 'app', 'display_name': manifest['name'],
                    'description': manifest.get('description', ''), 'is_public': True,
                    'repository_id': release['repository_id'], 'forked_from': release.get('forked_from'),
                    'version': manifest['version'], 'content': release['content'],
                    'readme': release['content'], 'changelog': changelog, 'app_release': release['app_release']}
            endpoint = f"packages/{published['id']}/versions" if published else 'packages'
            await request('POST', endpoint, json=data)
            package = await request('GET', 'packages/' + release['repository_id'])
            repository = package['repository']
            repository['clone_url'] = urljoin(base, repository['clone_url'])
            await asyncio.to_thread(manager.bind_publication, app_id, scope, release['repository_id'], repository)
            return {'success': True, 'repository': repository, 'tag': release['app_release']['tag'],
                    'commit': expected_commit}
        except httpx.HTTPError as exc:
            raise ValueError('Store connection failed; inspect the repository before retrying publication') from exc
