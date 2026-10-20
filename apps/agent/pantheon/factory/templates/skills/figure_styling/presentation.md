# Presentation Recipe

| Field | Value |
|---|---|
| UI outputType | `presentation` |
| Primary rendering mode | mixed |
| Best for | slide visuals, oral presentation figures, seminar slides |
| Do not use for | journal paper figures, conference posters, standalone data plots |
| Read path | `SKILL.md` + this file |

---

## Visual goal

Scientific slides that communicate one clear message per visual. Readable at distance, high contrast, minimal text. Combines code-rendered data panels with flat schematic visuals.

---

## Mixed slide schema

For scientific slides, prefer a three-zone 16:9 layout:

| Zone | Content |
|---|---|
| Left | code-rendered data panel (primary evidence) |
| Center | simplified summary plot or state map |
| Right | flat mechanism schematic or takeaway model |

Not every slide uses all three zones. Assign zones based on the message, not the template.

---

## Recipe

1. Identify the message the slide must communicate in one sentence.
2. Choose slide layout (data-only / schematic-only / mixed).
3. For data panels: use code rendering with presentation-scale defaults.
4. For schematic panels: use structured prompt with flat visual style.
5. Export PNG at 150 dpi minimum.
6. Run screen readability check.

---

## Style defaults (code panels)

```python
SLIDE_DEFAULTS = {
    # Size — wide format matches 16:9 slide
    "figsize": (8, 4.5),          # single panel, 16:9 ratio
    "dpi": 150,                   # screen: 150; print: 300

    # Typography — must be readable at 5–10 meters
    "font_family": "Arial",
    "font_size": 14,              # axis tick labels (slide must be larger than paper)
    "label_size": 16,             # axis labels
    "title_size": 18,
    "legend_fontsize": 12,

    # Lines and markers
    "linewidth": 2.0,
    "markersize": 7,

    # Axes
    "spine_top": False,
    "spine_right": False,
    "tick_length": 4,
    "tick_width": 0.8,
    "axis_linewidth": 1.0,

    # Grid
    "grid": False,

    # Color
    "palette": "colorblind",      # seaborn colorblind or tab10
    # Heatmap: 'RdBu_r' diverging

    # Export
    "output_formats": ["png"],
    "bbox_inches": "tight",
}
```

---

## Slide layout templates

### Layout A: full-width data panel
```
┌──────────────────────────────────────────────┐
│  Title                                       │
│  ┌──────────────────────────────────────┐    │
│  │         Code-rendered figure          │    │
│  └──────────────────────────────────────┘    │
│  One-line takeaway callout                   │
└──────────────────────────────────────────────┘
```

### Layout B: data + schematic
```
┌──────────────────────────────────────────────┐
│  Title                                       │
│  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Code panel   │  │  Flat schematic (AI) │  │
│  └──────────────┘  └──────────────────────┘  │
│  Callout                                     │
└──────────────────────────────────────────────┘
```

### Layout C: three-zone
```
┌──────────────────────────────────────────────┐
│  Title                                       │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Data     │  │ Summary  │  │ Schematic │  │
│  └──────────┘  └──────────┘  └───────────┘  │
└──────────────────────────────────────────────┘
```

---

## Schematic panel prompt scaffold (AI-first zone)

For schematic panels within mixed slides:

```
[STYLE]
Flat vector scientific illustration, white background, restrained palette, editable-looking, no decorative 3D, no photorealistic glow.

[SIZE]
Landscape, 4:3 or 16:9 panel proportion.

[LAYOUT]
[Describe the spatial arrangement of the mechanism or model.]

[ENTITIES]
[List concrete domain entities: molecules, cells, structures, states, conditions.]

[CONNECTIONS]
[Describe directional relationships: arrows for causation, brackets for comparison, dashed lines for association.]

[LABELS]
Use short labels. Maximum 3–5 words per label. Avoid complete sentences.

[NEGATIVE]
No fake charts, no decorative 3D, no photorealistic glow, no dense paragraphs, no unreadable labels, no generic clipart, no gradient backgrounds, no childish cartoon style.
```

---

## Screen readability

Slides need larger text than journal figures and must be legible from 5–10 meters.

- Title: ≤ 12 words
- Main callouts: max 3 per slide
- Axis labels: simplified, no full sentences
- Legends: minimal or directly labeled on plot
- Use high contrast (dark text on white, or white text on dark)
- Avoid compressing multi-panel paper figures into one slide
- Remove top and right spines
- No footnotes or fine print on slides

---

## Code stub (matplotlib)

```python
import matplotlib.pyplot as plt
import matplotlib as mpl
import seaborn as sns

mpl.rcParams.update({
    "font.family": "Arial",
    "font.size": 14,
    "axes.labelsize": 16,
    "axes.titlesize": 18,
    "axes.linewidth": 1.0,
    "xtick.major.size": 4,
    "xtick.major.width": 0.8,
    "ytick.major.size": 4,
    "ytick.major.width": 0.8,
    "legend.frameon": False,
    "legend.fontsize": 12,
})
palette = sns.color_palette("colorblind")

fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)

# --- plot here ---

ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.set_xlabel("X label", fontsize=16)
ax.set_ylabel("Y label", fontsize=16)

fig.tight_layout(pad=0.5)
fig.savefig("slide_panel.png", dpi=150, bbox_inches="tight")
```

---

## Domain-specific example: 3D genomics slide

For a 3D genomics presentation:
- Left data panel can show compartment switching (A/B), loop strength comparison, or TAD boundary insulation score across conditions.
- Center panel can show cell-state grouping or UMAP with condition labels.
- Right schematic can show chromatin architecture change (intact TAD vs disrupted boundary) leading to altered gene expression state.

This is only an example. The same layout applies to any domain.

---

## Negative constraints

Avoid:
- Generic AI art that looks like stock illustration
- Decorative 3D or photorealistic glow
- Dense text paragraphs on slides
- Fake charts or fake axes
- Unreadable labels at presentation scale
- Compressing paper-style multi-panel figures onto one slide
- Abstract boxes without domain meaning
- Gradient backgrounds
- Inconsistent icon styles

---

## Quick self-check

- [ ] One message per slide
- [ ] Font size ≥ 14pt for body, ≥ 16pt for axis labels
- [ ] High contrast — readable at distance
- [ ] Top/right spines removed
- [ ] Code panels use presentation-scale figsize and dpi
- [ ] Schematic panels use flat vector style
- [ ] No dense text blocks
- [ ] Exported as PNG
