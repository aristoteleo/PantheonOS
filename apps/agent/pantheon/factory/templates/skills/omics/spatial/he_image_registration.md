---
id: he_image_registration
name: Serial H&E Image Registration (RoMa)
description: |
  Register serial section H&E histology images using RoMa dense feature
  matching (DINOv2 backbone) and RANSAC rigid transform estimation. Aligns
  consecutive H&E images into a global coordinate frame for 3D reconstruction.
tags: [spatial, he, registration, roma, dinov2, histology]
---

# Serial H&E Image Registration with RoMa

Align serial H&E-stained histology sections into a shared coordinate frame for
3D tissue reconstruction. Traditional feature detectors (SIFT, ORB) struggle
with the repetitive textures and low contrast of histology images. RoMa uses a
DINOv2 backbone to produce dense, semantically-aware feature matches that are
far more robust on tissue sections.

## Prerequisites

> [!WARNING]
> RoMa requires a CUDA-capable GPU with at least 8 GB VRAM. CPU inference is
> prohibitively slow and not recommended.

```bash
pip install romatch torch torchvision
pip install opencv-python-headless tifffile pillow
```

- GPU required for RoMa inference
- Need RoMa checkpoint (`roma_outdoor.pth`) and DINOv2 weights (`dinov2_vitl14_pretrain.pth`)
- Download from RoMa GitHub repo: https://github.com/Parskatt/RoMa

## Workflow

### 1. Load Model

```python
import torch
from romatch import roma_outdoor

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

roma_model = roma_outdoor(
    device=device,
    coarse_res=560,
    upsample_res=(864, 1152),
)
H, W = roma_model.get_output_resolution()
```

### 2. Load Images and Masks

Apply tissue masks to remove background before matching. Background pixels
introduce spurious correspondences that degrade alignment quality.

```python
from PIL import Image
import numpy as np
import tifffile

img = np.array(Image.open("section_01.jpg").convert("RGB"))
label = tifffile.imread("section_01_mask.tiff")

# Remove background using tissue mask
img_clean = img.copy()
img_clean[label == 0] = 0
img_pil = Image.fromarray(img_clean)
```

### 3. Dense Feature Matching

RoMa produces a dense warp field and per-pixel certainty. Sample the top
keypoints from this field for downstream transform estimation.

```python
with torch.no_grad():
    warp, certainty = roma_model.match(img_a_pil, img_b_pil, device=device)

    matches, match_certainty = roma_model.sample(
        warp, certainty, num=3000
    )

    kpts_a, kpts_b = roma_model.to_pixel_coordinates(
        matches, H_A, W_A, H_B, W_B
    )
    kpts_a = kpts_a.cpu().numpy()
    kpts_b = kpts_b.cpu().numpy()
```

### 4. Rigid Transform Estimation (RANSAC)

Custom SVD-based estimation — rotation + translation only, no scale:

```python
def estimate_rigid_transform(kpts_a, kpts_b, ransac_threshold=5.0, max_trials=10000):
    best_inliers = 0
    best_T = None

    for _ in range(max_trials):
        idx = np.random.choice(len(kpts_a), 2, replace=False)

        ca = kpts_a[idx].mean(0)
        cb = kpts_b[idx].mean(0)

        H = (kpts_a[idx] - ca).T @ (kpts_b[idx] - cb)
        U, S, Vt = np.linalg.svd(H)
        R = Vt.T @ U.T
        if np.linalg.det(R) < 0:
            Vt[-1] *= -1
            R = Vt.T @ U.T
        t = cb - R @ ca

        errors = np.linalg.norm((R @ kpts_a.T).T + t - kpts_b, axis=1)
        inliers = errors < ransac_threshold

        if inliers.sum() > best_inliers:
            best_inliers = inliers.sum()
            best_T = (R, t, inliers)

    # Refine with all inliers
    R, t, inliers = best_T
    ca = kpts_a[inliers].mean(0)
    cb = kpts_b[inliers].mean(0)
    H = (kpts_a[inliers] - ca).T @ (kpts_b[inliers] - cb)
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1] *= -1
        R = Vt.T @ U.T
    t = cb - R @ ca

    T = np.eye(3)
    T[:2, :2] = R
    T[:2, 2] = t
    return T, inliers
```

**Key parameters:**

| Parameter | Value | Description |
|---|---|---|
| `coarse_res` | `560` | RoMa coarse matching resolution |
| `upsample_res` | `(864, 1152)` | Fine matching output resolution |
| `num` (sample) | `3000` | Keypoints to sample from dense field |
| `ransac_threshold` | `5.0` | Pixel tolerance for RANSAC inliers |
| `max_trials` | `10000` | Maximum RANSAC iterations |

### 5. Pairwise Alignment Loop

Run matching and transform estimation for each consecutive pair of sections.

```python
transforms = {}
for i in range(len(slice_indices) - 1):
    T, inliers = align_pair(images[i], images[i+1], roma_model)
    transforms[(slice_indices[i], slice_indices[i+1])] = T
    np.save(f"transform_{i:02d}_{i+1:02d}.npy", T)
```

### 6. Global Transform Composition (BFS)

Build a graph of pairwise transforms, then BFS from a reference slice to
compose global transforms. This approach is more robust than simple chain
accumulation because it can route around failed pairwise alignments.

```python
from collections import deque

graph = {}
for (src, dst), T in transforms.items():
    graph.setdefault(src, []).append((dst, T))
    graph.setdefault(dst, []).append((src, np.linalg.inv(T)))

ref_slice = slice_indices[0]
global_transforms = {ref_slice: np.eye(3)}
queue = deque([ref_slice])
visited = {ref_slice}

while queue:
    curr = queue.popleft()
    for neighbor, T_rel in graph.get(curr, []):
        if neighbor not in visited:
            global_transforms[neighbor] = T_rel @ global_transforms[curr]
            visited.add(neighbor)
            queue.append(neighbor)
```

> [!TIP]
> Include skip-one pairs (e.g., slice i to i+2) for redundancy. The BFS
> automatically finds the shortest path through the graph.

### 7. Apply Transforms and Warp

Use the inverse of each global transform to warp images into the reference
coordinate frame.

```python
import cv2

ref_shape = images[ref_slice].shape[:2]

for idx in slice_indices:
    M = np.linalg.inv(global_transforms[idx])[:2, :]
    warped = cv2.warpAffine(
        images[idx], M.astype(np.float32),
        (ref_shape[1], ref_shape[0]),
        flags=cv2.INTER_LINEAR,
        borderValue=(255, 255, 255)
    )
```

### 8. Visualize Alignment

Alpha blend overlapping images to verify registration quality.

```python
import matplotlib.pyplot as plt

alpha = 0.5
blended = cv2.addWeighted(warped_a, alpha, warped_b, 1 - alpha, 0)

fig, axes = plt.subplots(1, 3, figsize=(18, 6))
axes[0].imshow(warped_a)
axes[0].set_title("Section A (warped)")
axes[1].imshow(warped_b)
axes[1].set_title("Section B (warped)")
axes[2].imshow(blended)
axes[2].set_title("Alpha Blend Overlay")
for ax in axes:
    ax.axis("off")
plt.tight_layout()
plt.savefig("alignment_check.png", dpi=150, bbox_inches="tight")
plt.show()
```

## Common Pitfalls

1. **GPU required for RoMa**: CPU inference is extremely slow. Use GPU with
   at least 8 GB VRAM.
2. **Tissue mask quality**: Poor masks leak background matches. Use clean
   binary segmentation masks.
3. **180-degree rotated slices**: Some sectioning protocols produce alternating
   orientations. Pre-rotate before matching (`np.rot90(img, 2)`).
4. **Reference slice choice**: Pick a central, high-quality slice. Edge slices
   accumulate more error.
5. **Non-invertible transforms**: If a pairwise alignment fails completely
   (e.g., too few inliers), the global BFS will route around it via skip-pairs.
6. **Scale changes**: This method estimates pure rigid transforms (rotation +
   translation). If sections have different magnification, add scale estimation
   or pre-normalize.
7. **Large images**: RoMa resizes internally but still needs significant GPU
   memory for high-res H&E. Consider downsampling if OOM.
