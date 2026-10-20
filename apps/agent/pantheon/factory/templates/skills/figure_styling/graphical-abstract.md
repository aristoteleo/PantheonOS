# Graphical Abstract Recipe

## Purpose
Single-panel visual summary of a paper's key finding. Readers scan in <5 s; every element must earn its place.

---

## Visual Schema

```
┌─────────────────────────────────────────────────────────┐
│  TITLE ZONE (top, ≤12 words)                            │
├──────────────┬──────────────┬──────────────┬────────────┤
│  INPUT/      │   PROCESS /  │   OUTPUT /   │  IMPACT    │
│  CONTEXT     │   METHOD     │   RESULT     │  (opt)     │
│  (Zone A)    │   (Zone B)   │   (Zone C)   │  (Zone D)  │
├──────────────┴──────────────┴──────────────┴────────────┤
│  CAPTION ZONE (bottom, ≤20 words)                       │
└─────────────────────────────────────────────────────────┘
```

**Zone rules**
- Zone A–C are mandatory; Zone D is optional (use for clinical/translational work)
- Each zone = one visual unit (icon, schematic, chart, or micrograph)
- Connections between zones: directional arrows or gradient bands only

---

## Canvas Spec

| Property | Value |
|----------|-------|
| Size | 85 mm × 85 mm (square) or 170 mm × 85 mm (landscape) |
| Resolution | 300 dpi for print; 150 dpi for web |
| Background | White `#FFFFFF` or very light grey `#F8F8F8` |
| Safe margin | 4 mm all sides |

---

## Typography

| Element | Font | Size (pt) | Weight |
|---------|------|-----------|--------|
| Title | Sans-serif (Helvetica / Arial) | 9–10 | Bold |
| Zone label | Sans-serif | 7–8 | Regular |
| Data label | Sans-serif | 6–7 | Regular |
| Caption | Sans-serif | 6–7 | Regular |

- No more than **2 font sizes** in the main panel
- Avoid italics except for gene/species names

---

## Color Palette

Use a **3-color sequential or diverging palette** maximum:

```python
# Recommended palettes (colorblind-safe)
VIRIDIS_3  = ["#440154", "#21908C", "#FDE725"]
COOL_WARM  = ["#4575B4", "#FFFFBF", "#D73027"]
TEAL_AMBER = ["#009E73", "#F0E442", "#D55E00"]
```

- **Accent color**: one bold hue for the key result (Zone C)
- **Background objects**: desaturated or grey
- Never use red + green as the only distinguishing pair

---

## Icon & Illustration Guidelines

- Use **flat line icons** (stroke 1.5–2 pt); avoid gradients on icons
- Cell/tissue schematics: simplified, 2–3 color max
- Molecules: schematic ribbon/surface, not atomic detail
- Arrows: 2–3 pt weight; filled arrowhead; angle ≤45° preferred
- No drop shadows, glows, or 3D bevels

---

## Layout Constraints

1. **One message per zone** — do not split a concept across zones
2. **Left-to-right reading order** matches narrative flow
3. **Zone widths** may vary (e.g., 30 / 40 / 30) to weight the key result
4. **Whitespace** between zones ≥ 3 mm
5. If adding a mini bar/line chart: max 4 bars or 2 lines; no legend inside chart (use direct labels)

---

## Connection Symbols

| Relationship | Symbol |
|-------------|--------|
| Causes / leads to | `→` solid arrow |
| Inhibits | `⊣` flat-head arrow |
| Bidirectional | `↔` double arrow |
| Parallel process | horizontal bracket |
| Temporal sequence | numbered circles `①②③` |

---

## Domain-Specific Examples

### Genomics / Epigenomics
- Zone A: genome/chromatin schematic
- Zone B: assay pipeline icon (ChIP-seq, Hi-C loop)
- Zone C: heatmap or arc plot of key locus
- Accent color on regulatory element of interest

### Clinical / Translational
- Zone A: patient cohort icon
- Zone B: treatment/intervention schematic
- Zone C: Kaplan-Meier or forest plot thumbnail
- Zone D: clinical implication icon (organ, patient outcome)

### Cell Biology
- Zone A: cell cross-section with pathway entry
- Zone B: signaling cascade (3–4 nodes max)
- Zone C: phenotypic outcome (microscopy panel or bar chart)

---

## Matplotlib Quickstart

```python
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch

fig, axes = plt.subplots(1, 3, figsize=(6.7, 3.35))  # 170mm × 85mm at 100dpi
fig.patch.set_facecolor('#FFFFFF')

for ax in axes:
    ax.set_xticks([]); ax.set_yticks([])
    ax.spines[:].set_visible(False)

# Zone A — context icon (placeholder)
axes[0].text(0.5, 0.5, 'Zone A\nInput', ha='center', va='center',
             fontsize=9, fontweight='bold', color='#333333')

# Zone B — method schematic
axes[1].text(0.5, 0.5, 'Zone B\nProcess', ha='center', va='center',
             fontsize=9, fontweight='bold', color='#009E73')

# Zone C — key result
axes[2].text(0.5, 0.5, 'Zone C\nResult', ha='center', va='center',
             fontsize=9, fontweight='bold', color='#D55E00')

# Connecting arrows between subplots
for i in range(2):
    fig.add_artist(mpatches.FancyArrowPatch(
        (0.355 + i*0.315, 0.5), (0.38 + i*0.315, 0.5),
        transform=fig.transFigure,
        arrowstyle='->', mutation_scale=15,
        color='#555555', linewidth=1.5
    ))

fig.suptitle('Paper Title in ≤12 Words', fontsize=9, fontweight='bold', y=0.97)
plt.tight_layout(rect=[0, 0.03, 1, 0.93])
plt.savefig('graphical_abstract.png', dpi=300, bbox_inches='tight')
```

---

## Common Mistakes to Avoid

| Mistake | Fix |
|---------|-----|
| Too much text | ≤5 words per zone label |
| Inconsistent icon style | Pick one icon library / style |
| No clear left→right flow | Reorder zones; add directional arrows |
| Overloaded Zone C | Split into two sub-panels only if absolutely necessary |
| Low contrast labels | Ensure WCAG AA contrast ratio ≥4.5:1 |
| Missing scale bar on micrograph | Always add scale bar if using microscopy image |

---

## Checklist Before Export

- [ ] ≤3 colors used (+ black/white)
- [ ] All text ≥6 pt
- [ ] Arrows indicate direction clearly
- [ ] Title ≤12 words
- [ ] Caption ≤20 words
- [ ] 300 dpi / correct canvas size
- [ ] Colorblind-safe palette verified
- [ ] No copyrighted icons or clip art
