---
id: figure_format_lint
name: Figure Format Lint
description: |
  Check figure deliverables for format compliance: file naming, DPI,
  file format integrity, style_card consistency, caption presence,
  figure numbering continuity, and export completeness.
---

# Figure Format Lint

## When to Use

- Verification phase — before writing the manifest
- After critic loop exits — confirm deliverables are actually correct on disk
- Before any submission-mode export

## Checklist

### 1. File Naming (semantic, no raw paths)

- [ ] All filenames in `.canvas/assets/` are **semantic**: `Fig1_umap_celltypes.png`, not `output.png`, `test.png`, `tmp_fig.png`
- [ ] Filename contains figure ID: `Fig1_`, `Fig2_`, etc.
- [ ] No workdir path segments visible in filename (no `workspace_abc123_fig.png`)
- [ ] Multi-panel composite figures named with `_composite` suffix: `Fig1_composite.png`

### 2. File Format Integrity

- [ ] **PNG**: `file <path>` returns `PNG image data` — non-zero size
- [ ] **PDF** (if in `export_formats`): first 4 bytes are `%PDF` — non-zero size, version ≥ 1.4
- [ ] **SVG** (if in `export_formats`): file contains `<svg` root element — non-zero size
- [ ] No file is 0 bytes or a broken placeholder

### 3. DPI Compliance

- [ ] PNG DPI matches `style_card.dpi_final` (check with `identify -verbose <file> | grep Resolution` or PIL)
- [ ] `figure` / `graphical-abstract` scenario: DPI ≥ 600
- [ ] `poster` scenario: DPI ≥ 300
- [ ] `presentation` scenario: DPI ≥ 150

```python
from PIL import Image
img = Image.open(png_path)
dpi = img.info.get("dpi", (72, 72))
assert dpi[0] >= required_dpi, f"DPI {dpi[0]} < required {required_dpi}"
```

### 4. Style Card Compliance

- [ ] `font_size.axis_label` matches `aesthetic_guide` spec:
  - `nature_figure` → 7 pt; `neurips_*` → 9–10 pt; `ieee_figure` → 8 pt
- [ ] `categorical_palette` is Paul Tol bright or a named override (not `Dark2`)
- [ ] `export_formats` matches scenario defaults:
  - `figure` → `["png", "pdf", "svg"]`; `poster` → `["png", "pdf"]`; `presentation` → `["png"]`
- [ ] `dpi_final` is consistent with scenario (not accidentally left at 72 or 96)

### 5. Caption Completeness

- [ ] `figure_legends.md` exists at `{workdir}/.canvas/figure_legends.md`
- [ ] Every figure in `figure_manifest.json` has a corresponding `##` section in `figure_legends.md`
- [ ] No caption section is empty (check for `##` followed immediately by another `##`)
- [ ] Caption does not contain figure number prefix (`Figure 1:`, `Fig. 1.`) — numbering is handled by document template

### 6. Figure Numbering Continuity

- [ ] Figure IDs in `figure_manifest.json` are sequential: Fig1, Fig2, Fig3 … (no gaps, no duplicates)
- [ ] Panel letters within a composite figure are sequential: a, b, c … (no gaps)
- [ ] If supplementary figures exist: labeled `FigS1`, `FigS2`, not `Fig7`, `Fig8`

### 7. Export Completeness

For each figure in the manifest:
- [ ] PNG always present
- [ ] PDF present iff `"pdf" in style_card.export_formats`
- [ ] SVG present iff `"svg" in style_card.export_formats`
- [ ] No orphaned files in `.canvas/assets/` that are not in the manifest

### 8. No Caption Text Inside Images

- [ ] `observe_images` did not flag caption-looking text in any PNG
- [ ] Critic loop confirmed `caption_exclusion: pass` for all rounds

## Output (append to leader verification notes)

```json
{
  "format_lint": {
    "file_naming": "pass | fail",
    "file_integrity": "pass | fail",
    "dpi_compliance": "pass | fail",
    "style_card_compliance": "pass | fail",
    "caption_completeness": "pass | fail",
    "numbering_continuity": "pass | fail",
    "export_completeness": "pass | fail",
    "caption_not_in_image": "pass | fail",
    "blockers": ["list of failures that prevent delivery"],
    "warnings": ["list of non-blocking issues"]
  }
}
```

Any `"fail"` in `blockers` → do not deliver, fix first.
`"warnings"` are noted in the delivery summary but do not block.
