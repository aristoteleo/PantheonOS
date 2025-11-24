# Guideline for visualizing 3D spatial data

`pyvista` is a powerful library for visualizing 3D data, you can use it to visualize the 3D spatial data.

## Prerequisites

Install pyvista: `pip install pyvista`.

## Visualization

### Load the data

```python
adata = ... # 3D spatial data
# We assume the coordinates are stored in the adata.obsm["spatial"]
coords = adata.obsm["spatial"].copy()
```

### Visualize the expression of a gene in the 3D space

```python
import pyvista as pv

pv.set_jupyter_backend('static')

gene = "NEXN" # example gene
cloud = pv.PolyData(coords)
cloud['expression'] = adata[:, gene].X.flatten()
plotter = pv.Plotter()
plotter.add_points(
    cloud,
    render_points_as_spheres=False,
    point_size=1,
    cmap='RdYlBu_r',
    scalars='expression',
    opacity=0.1,
    clim=(0, 2.5),
    scalar_bar_args={
        "title": "Expression (normalized)",
        "color": "#FFFFFF",
        "n_colors": 20,
    }
)
# add the gene name to the plot
plotter.add_text(gene, font_size=20, color='#FFFFFF', position='upper_left')
# set the camera angle
plotter.camera.Elevation(-15)  # elevation angle
plotter.camera.Azimuth(-60)  # azimuth angle
# set the background color
plotter.set_background('black') # set the background color to black
plotter.show()
```

### Produce a video of the 3D spatial data

```python
plotter.open_gif("xxx.gif")  # save the gif to the current directory

n_frames = 30 # number of frames
for i in range(n_frames):
    plotter.camera.Azimuth(360 / n_frames)  # rotate the camera by 360/n_frames degrees
    plotter.write_frame()

plotter.close()
```

Show the gif in the notebook:

```python
from IPython.display import Image
Image(filename='xxx.gif')
```

### Visualize the cell type distribution in the 3D space

```python
import numpy as np
import seaborn as sns
import pyvista as pv

# 3D categorical plotting: visualize cell types in space
coords = adata.obsm["spatial"].copy()
coords[:, -1] = -coords[:, -1]

labels = adata.obs["celltype"].astype('category')
codes = labels.cat.codes.to_numpy()
n_cat = max(1, len(cat_order))
palette_colors = sns.color_palette(palette, n_colors=max(3, n_cat))[:n_cat]
color_map = np.array(palette_colors)

# Assign per-point RGB color; unknown (-1) to light gray
point_colors = np.ones((labels.shape[0], 3), dtype=float) * 0.8
valid = codes >= 0
if n_cat > 0 and valid.any():
    point_colors[valid] = color_map[codes[valid]]
point_colors_uint8 = (point_colors * 255).astype(np.uint8)

cloud = pv.PolyData(coords)
cloud['rgb'] = point_colors_uint8

p = pv.Plotter()
p.add_points(
    cloud,
    scalars='rgb',
    rgb=True,
    render_points_as_spheres=False,
    point_size=point_size,
    opacity=opacity,
)
p.set_background(background)
p.add_text(obs_key, font_size=label_font_size, color='#FFFFFF', position='upper_right')
p.camera.Elevation(-15)
p.camera.Azimuth(-60)

# Legend
legend = [(str(cat), tuple(color_map[i])) for i, cat in enumerate(cat_order)]
if len(legend) > 0:
    p.add_legend(legend, size=legend_size, loc="upper left")

    return p

p.show()
```