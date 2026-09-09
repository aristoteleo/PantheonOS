"""Bounded discovery of Python interpreters on the notebook execution host."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def clean_python_env():
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(key, None)
    return env


async def probe_python(python):
    # Keep the venv's executable path; resolving its symlink would select base Python.
    python = os.path.abspath(os.path.expanduser(python))
    if not os.path.isfile(python) or not os.access(python, os.X_OK):
        return {'success': False, 'error': 'Python executable not found or not executable.', 'python': python}
    code = """import sys,json,importlib.util
from importlib.metadata import version, PackageNotFoundError
packages = []
for name in ('scanpy','pertpy','anndata','numpy','pandas','scipy','matplotlib','scvi-tools','cellrank'):
    try: packages.append(name + ' ' + version(name))
    except PackageNotFoundError: pass
print(json.dumps(dict(python=sys.executable, prefix=sys.prefix, python_version='.'.join(map(str,sys.version_info[:3])), ipykernel=importlib.util.find_spec('ipykernel') is not None, key_packages=packages)))
"""
    try:
        result = await asyncio.to_thread(subprocess.run, [python, '-I', '-c', code], capture_output=True, text=True, timeout=8, env=clean_python_env())
        if result.returncode:
            raise ValueError(result.stderr.strip()[:300] or 'Interpreter did not start.')
        info = json.loads(result.stdout.strip())
        return {'success': True, **info, 'python': python}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {'success': False, 'python': python, 'error': f'Cannot use this Python interpreter: {error}'}


def _python_in(prefix):
    for relative in ('bin/python', 'bin/python3', 'python.exe', 'Scripts/python.exe'):
        path = Path(prefix) / relative
        if path.is_file():
            return str(path)


def _candidates(workdir, specs):
    candidates = {os.path.abspath(sys.executable): ('Default Python', 'runtime')}
    def add(path, label, source):
        if path and os.path.isfile(path):
            candidates.setdefault(os.path.abspath(path), (label, source))
    for name, info in specs.items():
        spec = info.get('spec', {})
        if spec.get('language', '').lower() != 'python':
            continue
        argv = spec.get('argv', [])
        if argv:
            add(shutil.which(argv[0]) if not os.path.isabs(argv[0]) else argv[0], spec.get('display_name', name), 'kernel')
    for name in ('python', 'python3'):
        add(shutil.which(name), name, 'PATH')
    roots = [Path(workdir), Path.home()]
    for key in ('VIRTUAL_ENV', 'CONDA_PREFIX'):
        if os.environ.get(key):
            add(_python_in(os.environ[key]), Path(os.environ[key]).name, 'environment')
    for root in roots:
        for name in ('.venv', 'venv', 'env', '.virtualenvs', '.conda/envs', 'miniconda3/envs', 'anaconda3/envs', 'micromamba/envs'):
            folder = root / name
            add(_python_in(folder), folder.name, 'venv')
            if folder.is_dir() and name not in ('.venv', 'venv', 'env'):
                for child in sorted(folder.iterdir())[:100]:
                    if child.is_dir():
                        add(_python_in(child), child.name, 'environment')
    # Project-local environments, without scanning large data/dependency trees.
    root = Path(workdir)
    if root.is_dir():
        for project in sorted(root.iterdir())[:200]:
            if project.is_dir() and not project.name.startswith('.') and project.name not in ('node_modules', 'data'):
                for name in ('.venv', 'venv'):
                    add(_python_in(project / name), f'{project.name}/{name}', 'workspace')
    for command in ('conda', 'micromamba', 'mamba'):
        executable = shutil.which(command)
        if not executable:
            continue
        try:
            result = subprocess.run([executable, 'env', 'list', '--json'], capture_output=True, text=True, timeout=8)
            for prefix in json.loads(result.stdout).get('envs', []):
                add(_python_in(prefix), Path(prefix).name, 'conda')
            break
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return candidates


async def discover_python_environments(workdir, specs):
    candidates = await asyncio.to_thread(_candidates, workdir, specs)
    limit = asyncio.Semaphore(6)
    async def probe(path, label, source):
        async with limit:
            return {**await probe_python(path), 'display_name': label, 'source': source}
    results = await asyncio.gather(*(probe(path, *description) for path, description in list(candidates.items())[:100]))
    unique = {}
    for info in results:
        if info['success']:
            unique.setdefault((os.path.realpath(info['prefix']), info['python_version']), info)
    return list(unique.values())
