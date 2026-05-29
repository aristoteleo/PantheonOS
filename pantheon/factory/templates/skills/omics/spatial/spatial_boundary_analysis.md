---
id: spatial_boundary_analysis
name: Spatial Signal Boundary Analysis
description: |
  Detect and quantify expression domain boundaries between two spatially
  antagonistic signals. Computes signed distance fields, auto-selects
  optimal boundary thresholds, and produces multi-panel diagnostic figures.
tags: [spatial, boundary, gradient, antagonism, contour]
---

# Spatial Signal Boundary Analysis

A generalizable method for analyzing spatial signal antagonism — detecting
expression domain boundaries between two spatially opposing signals. This applies
to any pair of antagonistic signals in spatially resolved data: morphogen
gradients restricting each other's domains (e.g., Cer1 restricting Nodal in
embryo anterior-posterior patterning), tumor-immune exclusion interfaces, or
any biological context where two signals define complementary spatial territories.

## Prerequisites

Uses only standard scientific Python packages. No special environment needed.

```bash
pip install scipy scikit-image matplotlib numpy
```

These are typically already available in any scanpy/anndata environment.

### 1. Prepare Signal Data

Extract two signals and spatial coordinates from an AnnData object. Signal A is
the inhibitor/restrictor and Signal B is the target whose domain is being shaped.

```python
import numpy as np
from scipy.spatial import cKDTree
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter, distance_transform_edt
from skimage.measure import find_contours
from scipy.spatial import ConvexHull
from matplotlib.path import Path
from scipy import stats

coords = adata.obsm['spatial'][:, :2]
signal_a = adata[:, 'Cer1'].X.flatten()  # inhibitor
signal_b = adata[:, 'Nodal'].X.flatten()  # target
```

Replace `'Cer1'` and `'Nodal'` with your gene pair of interest. If the
expression matrix is sparse, add `.toarray()` before `.flatten()`.

### 2. Grid Interpolation and Smoothing

Interpolate scattered cell data onto a regular grid, fill NaN regions, and
apply Gaussian smoothing to reveal domain-level patterns.

```python
grid_n = 260
sigma = 2.0

x, y = coords[:, 0], coords[:, 1]
xi = np.linspace(x.min(), x.max(), grid_n)
yi = np.linspace(y.min(), y.max(), grid_n)
X, Y = np.meshgrid(xi, yi)

A = griddata((x, y), signal_a, (X, Y), method="linear", fill_value=np.nan)
B = griddata((x, y), signal_b, (X, Y), method="linear", fill_value=np.nan)

# Fill NaNs with nearest neighbor
A = np.where(np.isnan(A), griddata((x, y), signal_a, (X, Y), method="nearest"), A)
B = np.where(np.isnan(B), griddata((x, y), signal_b, (X, Y), method="nearest"), B)

As = gaussian_filter(A, sigma=sigma)
Bs = gaussian_filter(B, sigma=sigma)
```

**Parameter guidance:**

| Parameter | Recommended Range | Effect |
|-----------|-------------------|--------|
| `grid_n` | 200-400 | Grid resolution. Use ~260 for ~5k cells per slice. Increase for denser data. |
| `sigma` | 1.5-3.5 | Gaussian smoothing width. Higher values merge adjacent domains. |

### 3. Create Tissue Mask

Build a convex hull from cell coordinates to exclude background regions from
the analysis. A slight inward shrink avoids edge artifacts.

```python
def tissue_mask_from_convex_hull(X, Y, coords, shrink=0.985):
    pts = np.unique(coords[:, :2], axis=0)
    hull = ConvexHull(pts)
    poly = pts[hull.vertices]
    center = poly.mean(axis=0, keepdims=True)
    poly = center + shrink * (poly - center)
    path = Path(poly)
    inside = path.contains_points(np.column_stack([X.ravel(), Y.ravel()]))
    return inside.reshape(X.shape)

tissue = tissue_mask_from_convex_hull(X, Y, coords)
```

### 4. Auto-Detect Expression Boundary

Scan percentile thresholds on signal A, compute a signed distance field at each
threshold, and select the boundary that maximizes Cohen's d effect size on
signal B across the boundary.

```python
def auto_boundary(X, Y, As, Bs, q_candidates=range(60, 96, 2), band_width_pixels=6):
    dx = (X[0, 1] - X[0, 0])
    w = band_width_pixels * dx
    best = None

    for q in q_candidates:
        thr = np.percentile(As, q)
        inside = As >= thr
        dist_out = distance_transform_edt(inside)
        dist_in = distance_transform_edt(~inside)
        sd = (dist_in - dist_out) * dx

        band = np.abs(sd) <= w
        in_band = band & (sd < 0)
        out_band = band & (sd > 0)

        if in_band.sum() < 200 or out_band.sum() < 200:
            continue

        mu_in, mu_out = np.mean(Bs[in_band]), np.mean(Bs[out_band])
        pooled = np.sqrt(0.5 * (np.std(Bs[in_band])**2 + np.std(Bs[out_band])**2))
        d = abs(mu_in - mu_out) / (pooled + 1e-12)

        if best is None or d > best[0]:
            best = (d, q, thr, sd)

    return best

result = auto_boundary(X, Y, As, Bs)
cohen_d, best_q, thr, signed_dist = result
print(f"Best boundary: percentile={best_q}, threshold={thr:.4f}, Cohen's d={cohen_d:.3f}")
```

### 5. Extract Boundary Contour

Use `skimage.measure.find_contours` on the thresholded signal A grid, masked to
the tissue region. Take the longest contour as the primary domain boundary.

```python
As_masked = As.copy()
As_masked[~tissue] = 0

contours = find_contours(As_masked, level=thr)
longest = max(contours, key=len)

# Convert grid indices to spatial coordinates
boundary_xy = np.column_stack([
    np.interp(longest[:, 1], np.arange(len(xi)), xi),
    np.interp(longest[:, 0], np.arange(len(yi)), yi),
])
```

### 6. Compute Signed Distance Per Cell

Assign each cell a signed distance to the boundary. The sign indicates which
side of the boundary the cell falls on (positive = signal A domain, negative =
signal B domain).

```python
def signed_distance_to_boundary(coords, boundary_xy):
    tree = cKDTree(boundary_xy)
    dists, idxs = tree.query(coords[:, :2])
    signed = np.zeros(len(coords))

    for i, (d, idx) in enumerate(zip(dists, idxs)):
        idx = int(idx)
        i0, i1 = max(0, idx - 1), min(len(boundary_xy) - 1, idx + 1)
        tangent = boundary_xy[i1] - boundary_xy[i0]
        normal = np.array([-tangent[1], tangent[0]])
        vec = coords[i, :2] - boundary_xy[idx]
        signed[i] = np.sign(np.dot(vec, normal)) * d

    return signed

sd_cells = signed_distance_to_boundary(coords, boundary_xy)

# Auto-orient: ensure signal_a high = positive side
if np.mean(signal_a[sd_cells < 0]) > np.mean(signal_a[sd_cells > 0]):
    sd_cells = -sd_cells

adata.obs['boundary_distance'] = sd_cells
```

### 7. Distance-Decay Analysis

For paracrine/secreted signals, analyze how target expression decays with
distance from source-expressing cells. This reveals the effective range of the
signal.

```python
def distance_decay_analysis(coords, source_signal, target_signal, source_percentile=80):
    thr = np.percentile(source_signal, source_percentile)
    sources = coords[source_signal >= thr, :2]
    targets = source_signal < thr

    tree = cKDTree(sources)
    dists, _ = tree.query(coords[targets, :2])

    bins = np.linspace(0, np.percentile(dists, 95), 20)
    centers = (bins[:-1] + bins[1:]) / 2
    bid = np.digitize(dists, bins) - 1

    means, sems = [], []
    for i in range(len(bins) - 1):
        m = bid == i
        if m.sum() > 3:
            vals = target_signal[targets][m]
            means.append(np.mean(vals))
            sems.append(np.std(vals) / np.sqrt(len(vals)))
        else:
            means.append(np.nan)
            sems.append(np.nan)

    return centers, np.array(means), np.array(sems)

centers, means, sems = distance_decay_analysis(coords, signal_a, signal_b)
```

### 8. Comprehensive Visualization (6-Panel Figure)

A diagnostic 6-panel figure covering spatial patterns, boundary quantification,
and statistical tests.

- **(A)** Signal A spatial expression with boundary contour overlay
- **(B)** Signal B spatial expression with boundary contour overlay
- **(C)** Signal B mean +/- SEM vs signed distance from boundary (binned)
- **(D)** Scatter plot of Signal A vs Signal B with Pearson correlation
- **(E)** Violin plot of Signal B on A-side vs B-side with Mann-Whitney U test
- **(F)** Dual overlay: Signal A in red channel, Signal B in blue channel

```python
import matplotlib.pyplot as plt
from scipy.stats import pearsonr, mannwhitneyu

gene_a_name = "Cer1"
gene_b_name = "Nodal"

fig, axes = plt.subplots(2, 3, figsize=(18, 11))

# (A) Signal A spatial + boundary
ax = axes[0, 0]
sc = ax.scatter(coords[:, 0], coords[:, 1], c=signal_a, s=4, cmap="Reds", alpha=0.8)
ax.plot(boundary_xy[:, 0], boundary_xy[:, 1], 'k-', lw=2)
ax.set_title(f"{gene_a_name} expression")
ax.set_aspect("equal")
plt.colorbar(sc, ax=ax, shrink=0.6)

# (B) Signal B spatial + boundary
ax = axes[0, 1]
sc = ax.scatter(coords[:, 0], coords[:, 1], c=signal_b, s=4, cmap="Blues", alpha=0.8)
ax.plot(boundary_xy[:, 0], boundary_xy[:, 1], 'k-', lw=2)
ax.set_title(f"{gene_b_name} expression")
ax.set_aspect("equal")
plt.colorbar(sc, ax=ax, shrink=0.6)

# (C) Signal B vs signed distance (binned)
ax = axes[0, 2]
n_bins = 30
bin_edges = np.linspace(np.percentile(sd_cells, 2), np.percentile(sd_cells, 98), n_bins + 1)
bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
bid = np.digitize(sd_cells, bin_edges) - 1
b_means, b_sems = [], []
for i in range(n_bins):
    m = bid == i
    if m.sum() > 3:
        b_means.append(np.mean(signal_b[m]))
        b_sems.append(np.std(signal_b[m]) / np.sqrt(m.sum()))
    else:
        b_means.append(np.nan)
        b_sems.append(np.nan)
b_means, b_sems = np.array(b_means), np.array(b_sems)
ax.fill_between(bin_centers, b_means - b_sems, b_means + b_sems, alpha=0.3, color="steelblue")
ax.plot(bin_centers, b_means, 'o-', color="steelblue", ms=3)
ax.axvline(0, color='k', ls='--', lw=1)
ax.set_xlabel("Signed distance from boundary")
ax.set_ylabel(f"Mean {gene_b_name}")
ax.set_title(f"{gene_b_name} vs boundary distance")

# (D) Scatter: Signal A vs Signal B
ax = axes[1, 0]
ax.scatter(signal_a, signal_b, s=2, alpha=0.3, color="gray")
r, p = pearsonr(signal_a, signal_b)
ax.set_xlabel(gene_a_name)
ax.set_ylabel(gene_b_name)
ax.set_title(f"Pearson r = {r:.3f}, p = {p:.2e}")

# (E) Violin: Signal B on each side
ax = axes[1, 1]
side_a = signal_b[sd_cells > 0]
side_b = signal_b[sd_cells < 0]
parts = ax.violinplot([side_a, side_b], positions=[1, 2], showmeans=True, showmedians=True)
ax.set_xticks([1, 2])
ax.set_xticklabels([f"{gene_a_name} side\n(n={len(side_a)})", f"{gene_b_name} side\n(n={len(side_b)})"])
ax.set_ylabel(f"{gene_b_name} expression")
stat, pval = mannwhitneyu(side_a, side_b, alternative="two-sided")
sig_label = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else "n.s."
y_max = max(np.percentile(side_a, 99), np.percentile(side_b, 99))
ax.plot([1, 2], [y_max * 1.05, y_max * 1.05], 'k-', lw=1)
ax.text(1.5, y_max * 1.08, f"{sig_label}\np={pval:.2e}", ha="center", fontsize=9)
ax.set_title(f"{gene_b_name} by boundary side")

# (F) Dual overlay: A=red, B=blue
ax = axes[1, 2]
a_norm = (signal_a - signal_a.min()) / (signal_a.max() - signal_a.min() + 1e-12)
b_norm = (signal_b - signal_b.min()) / (signal_b.max() - signal_b.min() + 1e-12)
rgb = np.column_stack([a_norm, np.zeros(len(a_norm)), b_norm])
rgb = np.clip(rgb, 0, 1)
ax.scatter(coords[:, 0], coords[:, 1], c=rgb, s=4, alpha=0.8)
ax.plot(boundary_xy[:, 0], boundary_xy[:, 1], 'w-', lw=2)
ax.set_title(f"Overlay: {gene_a_name} (red) / {gene_b_name} (blue)")
ax.set_aspect("equal")
ax.set_facecolor("black")

plt.tight_layout()
plt.savefig("boundary_analysis_6panel.png", dpi=200, bbox_inches="tight")
plt.show()
```

## Common Pitfalls

1. **`grid_n` too low**: Coarse grids miss fine boundary details. Start with 260
   for ~5k cells per slice, increase for denser data.
2. **`sigma` too high**: Over-smoothing merges adjacent domains. Use the 1.5-3.5
   range.
3. **Orientation ambiguity**: Auto-orientation compares mean signal on both sides.
   Verify visually that the positive side matches your expected signal_a domain.
4. **Convex hull shrink**: `shrink=0.985` excludes edge artifacts. Decrease if
   your tissue has concavities.
5. **NaN handling**: `griddata(method='linear')` produces NaN outside the convex
   hull of data points. Always fill with nearest-neighbor before smoothing.
6. **Boundary percentile selection**: The auto-detection scans percentiles 60-96.
   For weaker signals, widen the range by adjusting `q_candidates`.
