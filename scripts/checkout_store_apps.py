"""Opt-in checkouts for developing/testing Apps extracted from PantheonOS.

Never called at user boot or image build. Runtime installs go through Discover.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def checkout(base: str, destination: Path):
    catalog = json.loads((Path(__file__).resolve().parents[1] / 'pantheon/apps/official-store.json').read_text())
    for app in catalog['apps']:
        target = destination / app['id']
        if target.exists():
            print(f"Keep existing {target}")
            continue
        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination) as temp:
            stage = Path(temp) / 'app'
            subprocess.run(['git', 'clone', '--quiet', '--no-checkout',
                base.rstrip('/') + f"/api/store/repositories/{app['repository_id']}.git", str(stage)], check=True)
            subprocess.run(['git', '-C', str(stage), 'checkout', '--quiet', '--detach', app['commit']], check=True)
            manifest = json.loads((stage / 'app.json').read_text())
            if manifest['id'] != app['id']:
                raise ValueError('Store repository identity mismatch')
            stage.rename(target)
        print(f"Checked out {app['id']} {app['commit'][:8]}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store-url', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    checkout(args.store_url, args.destination)
