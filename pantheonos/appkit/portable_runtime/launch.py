"""Launch an App using its prepared environment without relocating the venv."""
import json
import os
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 4 or sys.argv[1] != '--install':
        raise RuntimeError('Expected --install followed by the App entry point')
    binding = json.loads((Path(sys.argv[2]) / 'python-environment.json').read_text())
    if binding.get('schema') != 1:
        raise RuntimeError('Unsupported App Python environment; reinstall this App')
    python = binding['python']
    resources = binding.get('runtime_resources')
    if resources:
        if (not isinstance(resources, dict) or type(resources.get('protocol')) is not int
                or resources['protocol'] != 1 or set(resources) != {'protocol', 'playwright'}
                or not isinstance(resources['playwright'], list) or not resources['playwright']
                or any(name not in ('chromium', 'firefox', 'webkit') for name in resources['playwright'])):
            raise RuntimeError('Unsupported App runtime resources; reinstall this App')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(python).parent.parent / 'browsers')
    os.execv(python, [python, *sys.argv[3:]])


if __name__ == '__main__':
    main()
