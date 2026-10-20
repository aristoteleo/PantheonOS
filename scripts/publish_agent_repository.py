"""Publish the Agent's source repository as the official `agent` App in the Store.

Each version is committed on top of the version published before it and
tagged v<version> (docs/agent/agent-app-repository.md §2), so the repository's
history is the Agent's release history. The source tree comes from
`general_agent_release --agent-source`. Requires a Store administrator token
in PANTHEON_STORE_TOKEN. A published version is never replaced. Run it with
this checkout on PYTHONPATH.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

import httpx

from pantheonos.apps.store_release import git, prepare_release

REPOSITORY_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://pantheonos.org/apps/agent'))


def publish(source: Path, base: str, token: str) -> dict:
    manifest = json.loads((source / 'app.json').read_text())
    if manifest.get('id') != 'agent':
        raise ValueError('This is not the Agent source repository')
    version = manifest['version']
    base = base.rstrip('/')
    with httpx.Client(base_url=base + '/api/store/', headers={'Authorization': 'Bearer ' + token},
                      timeout=300) as client, tempfile.TemporaryDirectory() as temp:
        permissions = client.get('apps/permissions')
        permissions.raise_for_status()
        if not permissions.json()['can_designate_official']:
            raise ValueError('Official publication requires a Store administrator')
        repo = Path(temp) / 'agent'
        existing = client.get(f'packages/{REPOSITORY_ID}')
        if existing.status_code == 200:
            versions = client.get(f'packages/{REPOSITORY_ID}/versions')
            versions.raise_for_status()
            prior = next((v for v in versions.json()['versions'] if v['version'] == version), None)
            if prior:
                print(f'agent {version} is already published', flush=True)
                return prior
            info = client.get(f'packages/{REPOSITORY_ID}/repository')
            info.raise_for_status()
            repository = info.json()['repository']
            clone_url = repository['clone_url']
            clone_url = clone_url if clone_url.startswith('http') else base + clone_url
            git(Path(temp), 'clone', '-q', clone_url, str(repo))
            git(repo, 'checkout', '-q', '-B', 'main', repository['head'])
        elif existing.status_code == 404:
            repo.mkdir()
            git(repo, 'init', '-q', '-b', 'main')
        else:
            existing.raise_for_status()
        for child in repo.iterdir():
            if child.name != '.git':
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(source, repo, dirs_exist_ok=True)
        git(repo, 'add', '-A')
        git(repo, '-c', 'user.name=Pantheon', '-c', 'user.email=apps@pantheon',
            'commit', '-q', '--allow-empty', '-m', f'Pantheon Agent {version}')
        git(repo, 'tag', 'v' + version)
        prepared = prepare_release(repo)
        payload = dict(name='pantheon-agent', type='app', display_name=manifest['name'],
                       description=manifest.get('description', ''), version=version, is_public=True,
                       repository_id=REPOSITORY_ID, category='apps', tags=['official', 'app'],
                       content=prepared['content'], readme=prepared['content'],
                       app_release=prepared['app_release'])
        response = (client.post(f'packages/{REPOSITORY_ID}/versions', json=payload) if existing.status_code == 200
                    else client.post('packages', json=payload))
        if response.status_code >= 400:
            raise RuntimeError(f'Store refused agent {version}: {response.status_code} {response.text[:500]}')
        commit = prepared['app_release']['commit']
        response = client.put('apps/agent/official', json={'repository_id': REPOSITORY_ID, 'expected_commit': commit})
        if response.status_code >= 400:
            raise RuntimeError(f'Official designation failed: {response.status_code} {response.text[:500]}')
        print(f"Published official agent {version} ({prepared['bundle_bytes']} bytes, commit {commit[:12]})", flush=True)
        return {'version': version, 'commit': commit, 'repository_id': REPOSITORY_ID}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Agent source tree (general_agent_release --agent-source)')
    parser.add_argument('--base-url', required=True)
    args = parser.parse_args()
    publish(args.source, args.base_url, os.environ['PANTHEON_STORE_TOKEN'])
