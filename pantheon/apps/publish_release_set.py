"""Publish an already released App release set to the Hub Store.

The Store records only the archive's immutable HTTPS URL, its SHA-256 and each
App's identity; the archive itself stays where it was released. The archive is
downloaded and verified first, so the published App list is exactly what a
platform will install. Publishing needs a Hub administrator's token
(PANTHEON_HUB_TOKEN); the first version creates the package.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile

from .release_set import _read_index


def release_content(root, *, url, sha256, version, platform):
    """The Store's release-set-v1 metadata for a verified release directory."""
    apps = {}
    for alias, variants in _read_index(Path(root))['apps'].items():
        variant = variants.get(platform)
        if variant is None:
            raise ValueError(f'{alias} has no {platform} package')
        apps[alias] = {'app_id': variant['app_id'], 'version': variant['version'], 'revision': variant['revision']}
    return {'format': 'release-set-v1', 'version': version, 'platform': platform,
            'url': url, 'sha256': sha256, 'apps': apps}


async def publish(hub, token, *, name, display_name, url, sha256, version, platform, changelog=None, cache=None):
    import httpx
    from pantheon.platform.release_source import release_set
    with tempfile.TemporaryDirectory() as temporary:
        root = await release_set(url, sha256, cache or temporary)
        content = json.dumps(release_content(root, url=url, sha256=sha256, version=version, platform=platform))
    headers = {'Authorization': f'Bearer {token}'}
    async with httpx.AsyncClient(base_url=hub.rstrip('/') + '/api/store', headers=headers, timeout=60) as client:
        existing = await client.get(f'/packages/{name}')
        if existing.status_code == 404:
            response = await client.post('/packages', json={
                'name': name, 'type': 'release', 'display_name': display_name, 'icon': '🧩', 'category': 'platform',
                'description': 'The complete set of startup Apps an owner Agent runs.',
                'version': version, 'content': content, 'changelog': changelog})
        else:
            existing.raise_for_status()
            if existing.json().get('type') != 'release':
                raise ValueError(f'{name} is not a release set package')
            response = await client.post(f"/packages/{existing.json()['id']}/versions",
                                         json={'version': version, 'content': content, 'changelog': changelog})
        if response.status_code != 200:
            raise ValueError(f'Publishing failed: HTTP {response.status_code} {response.text[:300]}')
        return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub', required=True)
    parser.add_argument('--name', default='pantheon-general-team')
    parser.add_argument('--display-name', default='Pantheon General Team')
    parser.add_argument('--url', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--platform', default='linux-amd64')
    parser.add_argument('--changelog')
    parser.add_argument('--cache')
    args = parser.parse_args()
    token = os.environ.get('PANTHEON_HUB_TOKEN')
    if not token:
        parser.error('Set PANTHEON_HUB_TOKEN to a Hub administrator token')
    result = asyncio.run(publish(args.hub, token, name=args.name, display_name=args.display_name, url=args.url,
                                 sha256=args.sha256, version=args.version, platform=args.platform,
                                 changelog=args.changelog, cache=args.cache))
    print(json.dumps({k: result.get(k) for k in ('package_id', 'id', 'name', 'version') if k in result}))


if __name__ == '__main__':
    main()
