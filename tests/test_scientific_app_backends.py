"""Scientific data adapters produce browser-readable stores with real libraries.

These checks run in the analysis environment; a minimal agent environment
without the scientific stack skips this module.
"""
import json
from pathlib import Path

import pytest

np = pytest.importorskip('numpy')
pd = pytest.importorskip('pandas')
ad = pytest.importorskip('anndata')
zarr = pytest.importorskip('zarr')

from apps.spatial3d.backend import _write_spatial_zarr
from apps.vitessce.backend import _convert
from apps.volume3d.backend import register


def small_adata():
    return ad.AnnData(
        X=np.arange(24, dtype='float32').reshape(8, 3),
        obs=pd.DataFrame({'cell_type': pd.Categorical(['A'] * 4 + ['B'] * 4)},
                         index=[f'cell{i}' for i in range(8)]),
        var=pd.DataFrame(index=['G1', 'G2', 'G3']),
        obsm={'spatial': np.arange(24, dtype='float32').reshape(8, 3),
              'X_umap': np.arange(16, dtype='float32').reshape(8, 2)},
    )


def test_vitessce_output_stays_zarr_v2_when_anndata_defaults_to_v3(tmp_path):
    source = tmp_path / 'cells.h5ad'
    small_adata().write_h5ad(source)
    original = source.read_bytes()
    result = _convert(source, tmp_path)
    store = Path(result['zarr_path'])
    assert json.loads((store / '.zgroup').read_text())['zarr_format'] == 2
    assert json.loads((store / 'X' / '.zarray').read_text())['shape'] == [8, 3]
    restored = ad.read_zarr(store)
    np.testing.assert_array_equal(restored.X, small_adata().X)
    assert list(restored.obs['cell_type'].cat.categories) == ['A', 'B']
    assert source.read_bytes() == original


def test_spatial_writer_accepts_current_zarr_and_preserves_data(tmp_path):
    output = tmp_path / 'cells.zarr'
    source = small_adata()
    result = _write_spatial_zarr(source, output)
    assert result == {'n_cells': 8, 'ndim': 3}
    meta = json.loads((output / '_spatial.json').read_text())
    assert meta['has_expression'] and meta['spatial_ndim'] == 3
    store = zarr.open_group(str(output), mode='r')
    np.testing.assert_array_equal(store['obsm/spatial'][:], source.obsm['spatial'])
    np.testing.assert_array_equal(store['obs/cell_type'][:], [0] * 4 + [1] * 4)
    assert json.loads((output / '.zgroup').read_text())['zarr_format'] == 2


@pytest.mark.asyncio
async def test_volume_retry_repairs_group_left_by_a_failed_writer(tmp_path):
    class Context:
        workspace = tmp_path
        methods = {}

        def method(self, function):
            self.methods[function.__name__] = function
            return function

        def log(self, _message):
            pass

        async def serve(self, path):
            return str(path)

    context = Context()
    register(context)
    output = tmp_path / '.pantheon/atrium-volume3d/example-v1/volume.ome.zarr'
    output.mkdir(parents=True)
    (output / '.zgroup').write_text('{"zarr_format":2}')
    result = await context.methods['load_dataset']('synthetic')
    assert Path(result['config']['url']) == output
    volume = zarr.open_group(str(output), mode='r')['0'][:]
    assert volume.shape == (96, 96, 96) and np.isfinite(volume).all()
    assert volume.max() > 0.5 and volume.min() >= 0
    assert json.loads((output / '.zattrs').read_text())['multiscales'][0]['version'] == '0.4'
