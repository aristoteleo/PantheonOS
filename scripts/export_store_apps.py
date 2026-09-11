"""Extract reviewed, tracked official App sources into independent Git repos.

This prepares release files only; it never publishes or reads user app scopes.
Run from an OS source checkout before removing the optional source trees.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid

from pantheon.apps.distribution import STORE_APPS
from pantheon.apps.store_release import git, prepare_release


def export(source: Path, output: Path):
    revision = git(source, 'rev-parse', 'HEAD').strip()
    catalog = []
    for app_id in sorted(STORE_APPS):
        root = output / app_id
        paths = git(source, 'ls-files', '-z', f'apps/{app_id}/').split('\0')
        if not root.exists():
            root.mkdir(parents=True)
            for path in filter(None, paths):
                relative = Path(path).relative_to(f'apps/{app_id}')
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=source))
            # Record the license of first-party integration code. Bundled
            # third-party notices and their own licenses remain in each App.
            license_path = next((p for p in ('LICENSE', 'LICENSE.md') if (source / p).is_file()), None)
            if license_path and not (root / 'LICENSE').exists():
                (root / 'LICENSE').write_bytes((source / license_path).read_bytes())
            manifest = json.loads((root / 'app.json').read_text())
            if not (root / 'README.md').exists():
                (root / 'README.md').write_text(
                    f"# {manifest['name']}\n\n{manifest.get('description', '')}\n\n"
                    "Install this App from **Discover** in Pantheon Store. "
                    "Its source, assets and agent skills are versioned in this repository.\n\n"
                    "Use **Fork repository** to develop your own version. Commit and tag a version "
                    "to run it locally; **Publish** shares that release in Discover. "
                    "**Contribute** proposes a reviewed merge into an upstream repository.\n\n"
                    + (f"## Agent usage\n\nSee [{manifest['skill']}]({manifest['skill']}).\n" if manifest.get('skill') else '')
                )
            git(root, 'init', '-q', '-b', 'main')
            git(root, 'add', '-A')
            git(root, '-c', 'user.name=Pantheon', '-c', 'user.email=apps@pantheon',
                'commit', '-qm', f'Extract {app_id} from PantheonOS {revision}')
            git(root, 'tag', 'v' + manifest['version'])
        prepared = prepare_release(root)
        manifest = prepared['manifest']
        repository_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'https://pantheonos.org/apps/{app_id}'))
        payload = dict(name=f'pantheon-{app_id}', type='app', display_name=manifest['name'],
                       description=manifest.get('description', ''), version=manifest['version'],
                       is_public=True, repository_id=repository_id, category='apps', tags=['official', 'app'],
                       content=prepared['content'], readme=prepared['content'], app_release=prepared['app_release'])
        (output / f'{app_id}.release.json').write_text(json.dumps(payload))
        catalog.append(dict(id=app_id, repository_id=repository_id, name=payload['name'],
                            version=manifest['version'], commit=prepared['app_release']['commit'],
                            sha256=prepared['app_release']['sha256']))
        print(f"Prepared {app_id} {manifest['version']} ({prepared['bundle_bytes']} bytes)")
    (output / 'catalog.json').write_text(json.dumps({'source_revision': revision, 'apps': catalog}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    export(Path(__file__).resolve().parents[1], args.output.resolve())
