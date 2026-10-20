---
id: visualize_3d_spatial
name: 3D Spatial Data Visualization
description: |
  Visualize 3D spatial transcriptomics data using PyVista.
  Create interactive 3D plots and rotating animations.
tags: [spatial, 3d, visualization, pyvista]
---

# Visualizing 3D Spatial Data with PyVista

PyVista is a powerful library for 3D visualization. Use it to explore
3D spatial transcriptomics data with interactive plots and animations.

## Prerequisites

```bash
pip install pyvista
```

## Setup

```python
import numpy as np
import pyvista as pv
import seaborn as sns

# For notebooks: use static backend
pv.set_jupyter_backend('static')
```

## Load Coordinates

```python
adata = ...  # 3D spatial data (AnnData)
coords = adata.obsm["spatial"].copy()

# Optional: flip Z axis if needed
# coords[:, -1] = -coords[:, -1]
```

## Visualize Gene Expression

Create a 3D point cloud colored by gene expression:

```python
def plot_gene_3d(adata, gene, coords=None, 
                 point_size=1, opacity=0.1,
                 cmap='RdYlBu_r', clim=(0, 2.5),
                 background='black', text_color='#FFFFFF',
                 elevation=-15, azimuth=-60):
    """
    Plot gene expression in 3D space.
    
    Args:
        adata: AnnData with spatial coordinates
        gene: Gene name to visualize
        coords: Coordinate array (default: adata.obsm["spatial"])
        point_size: Size of points
        opacity: Point opacity
        cmap: Colormap name
        clim: Color limits (min, max)
    """
    if coords is None:
        coords = adata.obsm["spatial"].copy()
    
    # Create point cloud
    cloud = pv.PolyData(coords)
    cloud['expression'] = adata[:, gene].X.toarray().flatten()
    
    # Create plotter
    plotter = pv.Plotter()
    plotter.add_points(
        cloud,
        render_points_as_spheres=False,
        point_size=point_size,
        cmap=cmap,
        scalars='expression',
        opacity=opacity,
        clim=clim,
        scalar_bar_args={
            "title": "Expression",
            "color": text_color,
            "n_colors": 20,
        }
    )
    
    # Add gene name label
    plotter.add_text(gene, font_size=20, color=text_color, position='upper_left')
    
    # Set camera angle
    plotter.camera.Elevation(elevation)
    plotter.camera.Azimuth(azimuth)
    
    # Set background
    plotter.set_background(background)
    
    return plotter

# Usage
plotter = plot_gene_3d(adata, "NEXN")
plotter.show()
```

## Visualize Cell Types

Color points by categorical labels:

```python
def plot_celltype_3d(adata, obs_key='celltype', coords=None,
                     palette='tab20', point_size=1, opacity=0.3,
                     background='black', legend_size=(0.2, 0.3),
                     elevation=-15, azimuth=-60):
    """
    Plot cell types in 3D space.
    
    Args:
        adata: AnnData with spatial coordinates
        obs_key: Column in adata.obs with cell type labels
        coords: Coordinate array (default: adata.obsm["spatial"])
        palette: Seaborn palette name
    """
    if coords is None:
        coords = adata.obsm["spatial"].copy()
    
    # Get labels and create color mapping
    labels = adata.obs[obs_key].astype('category')
    cat_order = labels.cat.categories.tolist()
    codes = labels.cat.codes.to_numpy()
    n_cat = len(cat_order)
    
    # Generate colors
    palette_colors = sns.color_palette(palette, n_colors=max(3, n_cat))[:n_cat]
    color_map = np.array(palette_colors)
    
    # Assign per-point RGB color
    point_colors = np.ones((len(labels), 3), dtype=float) * 0.8  # Default gray
    valid = codes >= 0
    if n_cat > 0 and valid.any():
        point_colors[valid] = color_map[codes[valid]]
    point_colors_uint8 = (point_colors * 255).astype(np.uint8)
    
    # Create point cloud
    cloud = pv.PolyData(coords)
    cloud['rgb'] = point_colors_uint8
    
    # Create plotter
    plotter = pv.Plotter()
    plotter.add_points(
        cloud,
        scalars='rgb',
        rgb=True,
        render_points_as_spheres=False,
        point_size=point_size,
        opacity=opacity,
    )
    
    # Styling
    plotter.set_background(background)
    plotter.add_text(obs_key, font_size=20, color='#FFFFFF', position='upper_right')
    plotter.camera.Elevation(elevation)
    plotter.camera.Azimuth(azimuth)
    
    # Add legend
    legend = [(str(cat), tuple(color_map[i])) for i, cat in enumerate(cat_order)]
    if len(legend) > 0:
        plotter.add_legend(legend, size=legend_size, loc="upper left")
    
    return plotter

# Usage
plotter = plot_celltype_3d(adata, 'celltype')
plotter.show()
```

## Create Rotating Animation

Generate a GIF of the 3D plot rotating:

```python
def create_rotation_gif(plotter, output_path, n_frames=30):
    """
    Create a rotating GIF from a PyVista plotter.
    
    Args:
        plotter: Configured PyVista plotter
        output_path: Path to save GIF
        n_frames: Number of frames (determines rotation speed)
    """
    plotter.open_gif(output_path)
    
    for i in range(n_frames):
        plotter.camera.Azimuth(360 / n_frames)
        plotter.write_frame()
    
    plotter.close()
    print(f"Saved animation to {output_path}")

# Usage
plotter = plot_gene_3d(adata, "NEXN")
create_rotation_gif(plotter, "nexn_expression_3d.gif", n_frames=30)
```

## Display GIF in Notebook

```python
from IPython.display import Image
Image(filename='nexn_expression_3d.gif')
```

## Save Static Image

```python
# Screenshot to file
plotter.screenshot("plot_3d.png")

# Or with specific resolution
plotter.screenshot("plot_3d_hires.png", window_size=[1920, 1080])
```

## Tips for Large Datasets

> [!TIP]
> For datasets with millions of points:
> - Reduce `point_size` (e.g., 0.5 or smaller)
> - Increase `opacity` slightly for visibility
> - Consider subsampling for initial exploration
> - Use `render_points_as_spheres=False` for better performance

```python
# Subsample for quick visualization
sample_idx = np.random.choice(len(adata), 50000, replace=False)
adata_sample = adata[sample_idx]
```

## Camera Angles Reference

Common camera configurations:
- **Top-down**: `Elevation(-90), Azimuth(0)`
- **Side view**: `Elevation(0), Azimuth(0)`
- **Isometric**: `Elevation(-30), Azimuth(-45)`
- **Heart-like (default)**: `Elevation(-15), Azimuth(-60)`
