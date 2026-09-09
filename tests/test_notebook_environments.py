import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nbformat
import pytest

from pantheon.apps.builtin.notebook.integrated_notebook import IntegratedNotebookToolSet, NotebookContext
from pantheon.apps.builtin.notebook import python_environments
from pantheon.apps.builtin.notebook.jupyter_kernel import JupyterKernelToolSet


def notebook(tmp_path):
    ts = IntegratedNotebookToolSet('environment-test', workdir=str(tmp_path), streaming_mode='local')
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('value = 42')]), tmp_path / 'a.ipynb')
    ts.get_session_id = lambda: 'desktop'
    ts.notebook_contexts[('a.ipynb', 'desktop')] = NotebookContext('a.ipynb', 'desktop', 'old', 'now', 'a')
    ts.kernel_toolset.sessions['old'] = SimpleNamespace(status=SimpleNamespace(value='idle'), execution_count=1)
    ts.kernel_toolset.shutdown_session = AsyncMock(return_value={'success': True})
    return ts


@pytest.mark.asyncio
async def test_invalid_or_missing_ipykernel_keeps_current_environment(tmp_path, monkeypatch):
    ts = notebook(tmp_path)
    probe = AsyncMock(return_value={'success': True, 'ipykernel': False, 'python': '/env/bin/python'})
    monkeypatch.setattr(python_environments, 'probe_python', probe)
    result = await ts._select_python_environment('a.ipynb', '/env/bin/python')
    assert result['code'] == 'missing_ipykernel'
    assert ts.notebook_contexts[('a.ipynb', 'desktop')].kernel_session_id == 'old'
    ts.kernel_toolset.shutdown_session.assert_not_awaited()
    assert not (await ts._select_python_environment('a.ipynb', 'relative/python'))['success']


@pytest.mark.asyncio
async def test_switch_failure_does_not_destroy_working_kernel(tmp_path, monkeypatch):
    ts = notebook(tmp_path)
    monkeypatch.setattr(python_environments, 'probe_python', AsyncMock(return_value={
        'success': True, 'ipykernel': True, 'python': '/env/bin/python', 'prefix': '/env', 'python_version': '3.12.1'}))
    ts._setup_kernel = AsyncMock(return_value={'success': True, 'kernel_name': 'test-env'})
    ts.kernel_toolset.create_session = AsyncMock(return_value={'success': False, 'error': 'new kernel could not start'})
    result = await ts._select_python_environment('a.ipynb', '/env/bin/python')
    assert not result['success']
    assert ts.notebook_contexts[('a.ipynb', 'desktop')].kernel_session_id == 'old'
    ts.kernel_toolset.shutdown_session.assert_not_awaited()
    assert not ts._switching_notebooks


@pytest.mark.asyncio
async def test_busy_notebook_cannot_switch(tmp_path):
    ts = notebook(tmp_path)
    with ts._notebook_execution('a.ipynb'):
        result = await ts._select_python_environment('a.ipynb', sys.executable)
    assert not result['success'] and 'running cell' in result['error']
    ts.kernel_toolset.shutdown_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_switch_preserves_cells_and_persists_environment(tmp_path, monkeypatch):
    ts = notebook(tmp_path)
    path = tmp_path / 'a.ipynb'
    original = nbformat.read(path, as_version=4)
    original.cells[0].outputs = [nbformat.v4.new_output('stream', name='stdout', text='saved output\n')]
    original.metadata['custom'] = {'keep': True}
    nbformat.write(original, path)
    monkeypatch.setattr(python_environments, 'probe_python', AsyncMock(return_value={
        'success': True, 'ipykernel': True, 'python': '/project/.venv/bin/python',
        'prefix': '/project/.venv', 'python_version': '3.12.1'}))
    ts._setup_kernel = AsyncMock(return_value={'success': True, 'kernel_name': 'project-env'})
    ts.kernel_toolset.create_session = AsyncMock(return_value={'success': True, 'session_id': 'new'})

    result = await ts._select_python_environment('a.ipynb', '/project/.venv/bin/python')
    assert result['success']
    assert ts.notebook_contexts[('a.ipynb', 'desktop')].kernel_session_id == 'new'
    ts.kernel_toolset.shutdown_session.assert_awaited_once_with('old')
    saved = nbformat.read(path, as_version=4)
    assert saved.cells == original.cells
    assert saved.metadata.custom == original.metadata.custom
    assert saved.metadata.kernelspec.name == 'project-env'
    assert 'project/.venv' in saved.metadata.kernelspec.display_name

    # A new chat context must use the notebook's saved interpreter, not python3.
    ts.notebook_contexts.clear()
    ts.kernel_toolset.create_session.reset_mock()
    context = await ts._get_or_create_context('a.ipynb', 'another-chat')
    assert context.kernel_spec == 'project-env'
    ts.kernel_toolset.create_session.assert_awaited_once_with('project-env', cwd=str(tmp_path))


def test_venv_symlinks_are_distinct_package_environments(tmp_path):
    folder = tmp_path / '.venv'
    (folder / 'bin').mkdir(parents=True)
    (folder / 'pyvenv.cfg').write_text('home = /python')
    python = folder / 'bin' / 'python'
    python.symlink_to(sys.executable)
    ts = JupyterKernelToolSet.__new__(JupyterKernelToolSet)
    assert os.path.realpath(python) == os.path.realpath(sys.executable)
    assert not ts._is_same_interpreter(str(python))


def test_generic_default_kernel_reports_actual_interpreter(tmp_path, monkeypatch):
    from jupyter_client.kernelspec import KernelSpecManager
    monkeypatch.setattr(KernelSpecManager, 'get_kernel_spec', lambda *_: SimpleNamespace(
        argv=['python', '-m', 'ipykernel_launcher'], display_name='Python 3'))
    assert notebook(tmp_path)._kernel_details('python3')['kernel_python'] == sys.executable


def test_discovery_keeps_two_project_venvs(tmp_path, monkeypatch):
    for name in ('one', 'two'):
        folder = tmp_path / name / '.venv' / 'bin'
        folder.mkdir(parents=True)
        (folder / 'python').symlink_to(sys.executable)
    monkeypatch.setenv('PATH', '')
    paths = python_environments._candidates(tmp_path, {})
    assert str(tmp_path / 'one/.venv/bin/python') in paths
    assert str(tmp_path / 'two/.venv/bin/python') in paths
