import json

import nbformat
import pytest

from pantheon.apps.builtin.notebook.integrated_notebook import IntegratedNotebookToolSet, NotebookContext


@pytest.mark.asyncio
async def test_rename_preserves_contents_and_kernel_contexts(tmp_path):
    ts = IntegratedNotebookToolSet('rename-test', workdir=str(tmp_path), streaming_mode='local')
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('value = 42')])
    source = tmp_path / 'Untitled.ipynb'
    nbformat.write(nb, source)
    ctx = NotebookContext('Untitled.ipynb', 'desktop', 'kernel-1', 'now', 'Untitled')
    ts.notebook_contexts[('Untitled.ipynb', 'desktop')] = ctx
    result = await ts.rename_notebook('Untitled.ipynb', 'Analysis')
    assert result['success'] and result['notebook_path'] == 'Analysis.ipynb'
    assert not source.exists()
    assert nbformat.read(tmp_path / 'Analysis.ipynb', as_version=4).cells[0].source == 'value = 42'
    assert ts.notebook_contexts[('Analysis.ipynb', 'desktop')] is ctx
    assert ctx.kernel_session_id == 'kernel-1'
    assert ('Untitled.ipynb', 'desktop') not in ts.notebook_contexts
    saved = json.loads(ts.persistence_file.read_text())
    assert 'Analysis.ipynb::desktop' in saved['contexts']


@pytest.mark.asyncio
async def test_rename_rejects_collision_path_escape_and_running_kernel(tmp_path):
    ts = IntegratedNotebookToolSet('rename-test', workdir=str(tmp_path), streaming_mode='local')
    source = tmp_path / 'a.ipynb'
    nbformat.write(nbformat.v4.new_notebook(), source)
    (tmp_path / 'taken.ipynb').write_text('keep me')
    assert not (await ts.rename_notebook('a.ipynb', 'taken.ipynb'))['success']
    assert (tmp_path / 'taken.ipynb').read_text() == 'keep me'
    assert not (await ts.rename_notebook('a.ipynb', '../escape'))['success']
    assert not (await ts.rename_notebook('../a.ipynb', 'escape'))['success']
    ts.notebook_contexts[('a.ipynb', 'desktop')] = NotebookContext('a.ipynb', 'desktop', 'kernel-1', 'now', 'a')
    async with ts.kernel_toolset._get_execution_lock('kernel-1'):
        result = await ts.rename_notebook('a.ipynb', 'renamed')
    assert not result['success'] and 'running cell' in result['error']
    assert source.exists() and not (tmp_path / 'renamed.ipynb').exists()


@pytest.mark.asyncio
async def test_rename_waits_for_execution_output_to_finish_saving(tmp_path):
    ts = IntegratedNotebookToolSet('rename-test', workdir=str(tmp_path), streaming_mode='local')
    source = tmp_path / 'a.ipynb'
    nbformat.write(nbformat.v4.new_notebook(), source)
    with ts._notebook_execution('a.ipynb'):
        result = await ts.rename_notebook('a.ipynb', 'renamed')
        assert not result['success'] and source.exists()
    assert (await ts.rename_notebook('a.ipynb', 'renamed'))['success']
