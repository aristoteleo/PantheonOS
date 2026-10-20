---
id: stardist_segmentation
name: Nuclear Segmentation with StarDist
tags: [segmentation, stardist, nucleus, star-convex]
tested_on: embryo DAPI image (1024x1024 uint16)
test_results: 0.5s, 150 cells detected (vs 955 for Cellpose)
test_notes: StarDist dramatically under-segments this image because cells are not perfectly round/convex.
---

# Nuclear Segmentation with StarDist

### 1. Prerequisites
```bash
pip install stardist tensorflow
```
Note: Requires TensorFlow. On macOS, `pip install tensorflow-macos` may be needed.

### 2. Basic Segmentation
```python
from stardist.models import StarDist2D
from csbdeep.utils import normalize

model = StarDist2D.from_pretrained('2D_versatile_fluo')
labels, details = model.predict_instances(normalize(img))
# labels: (H, W) integer array
# details: dict with 'coord', 'points', 'prob'
```

### 3. Available Pretrained Models
| Model | Description |
|---|---|
| `2D_versatile_fluo` | Fluorescence microscopy (recommended default) |
| `2D_versatile_he` | H&E histology |
| `2D_paper_dsb2018` | Data Science Bowl 2018 nuclei |
| `2D_demo` | Small demo model |

### 4. 3D Segmentation
```python
from stardist.models import StarDist3D
model = StarDist3D.from_pretrained('3D_demo')
labels, details = model.predict_instances(normalize(volume))
```

### 5. Custom Training
```python
from stardist import fill_label_holes
from stardist.models import Config2D, StarDist2D

conf = Config2D(n_rays=32, grid=(2,2), n_channel_in=1)
model = StarDist2D(conf, name='my_model', basedir='models')
model.train(X_train, Y_train, validation_data=(X_val, Y_val), epochs=100)
```

### 6. Adjusting Detection Sensitivity
```python
labels, details = model.predict_instances(
    normalize(img),
    prob_thresh=0.5,   # increase to reduce false positives (default ~0.48)
    nms_thresh=0.3,    # NMS IoU threshold (default 0.3)
)
```

### Common Pitfalls
1. **Star-convex assumption**: StarDist assumes nuclei are star-convex (roughly round). It severely under-segments elongated, irregular, or concave cells. In our test: 150 cells vs 955 for Cellpose on the same image.
2. **Best for round nuclei**: Excellent speed (0.5s vs 310s for Cellpose) and accuracy WHEN nuclei are round (e.g., DAPI-stained isolated nuclei). Poor for cytoplasm or irregular shapes.
3. **TensorFlow dependency**: Requires TensorFlow, which can conflict with PyTorch-based tools. Consider separate environments.
4. **normalize() required**: Always use `csbdeep.utils.normalize` before prediction. Raw uint16 images without normalization produce garbage results.
5. **n_rays parameter**: More rays = better shape approximation but slower. Default 32 is good for round nuclei; increase to 64+ for slightly irregular shapes (won't help with truly non-convex).
