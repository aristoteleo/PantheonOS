"""Target-node, private Python environment. Never modifies the system Python."""
import argparse
import contextlib
import traceback
import json
import subprocess
import sys
import venv
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--package', type=Path, required=True)
    ap.add_argument('--install', type=Path, required=True)
    args = ap.parse_args()
    args.install.mkdir(parents=True, exist_ok=True)
    log_path = args.install / 'dependencies.log'
    try:
        with log_path.open('w') as log, contextlib.redirect_stderr(log):
            if sys.version_info < (3, 10):
                raise RuntimeError('Python 3.10 or newer is required on this node')
            root = args.install / 'venv'
            venv.EnvBuilder(with_pip=True, symlinks=sys.platform != 'win32').create(root)
            python = root / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            requirements = next((args.package / n for n in ('backend/requirements.txt', 'requirements.txt')
                                 if (args.package / n).is_file()), None)
            if requirements:
                subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                                '-r', str(requirements)], cwd=args.package, check=True, stdout=log, stderr=log)
    except Exception:
        with log_path.open('a') as log:
            traceback.print_exc(file=log)
        print(json.dumps({'status': 'failed', 'message':
            f'Could not prepare Python dependencies on this node. Details: {log_path}'}))
        return
    print(json.dumps({'status': 'succeeded', 'message': 'Private Python environment prepared on this node'}))


if __name__ == '__main__':
    main()
