# NeurIPS 2025 Methodology Diagram Aesthetic Guideline

> **Attribution**: This guideline is adapted from the PaperBanana project
> ([github.com/dwzhu-pku/PaperBanana](https://github.com/dwzhu-pku/PaperBanana),
> Apache-2.0), specifically `style_guides/neurips2025_diagram_style_guide.md`.
> It was automatically synthesized from 292 NeurIPS 2025 methodology diagrams
> via the method described in Zhu et al., *PaperBanana: Automating Academic
> Illustration for AI Scientists*, arXiv:2601.23265. Use this as a reference
> for publication-quality methodology / framework / pipeline diagrams.

## 1. The "NeurIPS Look"

The prevailing 2026 aesthetic for methodology diagrams is **"Soft Tech & Scientific Pastels."** Harsh primary colors and sharp black boxes are out; the modern academic diagram feels **approachable yet precise**. Use high-value (light) backgrounds to organize complexity, reserving saturation for critical active elements. Aim for **clean modularity** (clear separation of parts) with **narrative flow** (clear left-to-right progression).

## 2. Detailed Style Options

### A. Color Palettes
*Design philosophy: Use color to group logic, not just to decorate. Avoid fully saturated backgrounds.*

**Background fills (the "zone" strategy)** — used to encapsulate stages (e.g., "Pre-training phase") or environments.
- Very light, desaturated pastels (opacity ~10–15%). Aesthetically pleasing options:
  - 🍦 **Cream / Beige** `#F5F5DC` — warm, academic feel
  - ☁️ **Pale Blue / Ice** `#E6F3FF` — clean, technical feel
  - 🌿 **Mint / Sage** `#E0F2F1` — soft, organic feel
  - 🌸 **Pale Lavender** `#F3E5F5` — distinctive, modern feel
- Alternative: white backgrounds with colored *dashed borders* for a high-contrast minimalist look (common in theoretical papers).

**Functional element colors**
- **Active modules** (encoders, MLP, attention): medium saturation. Common pairings: Blue/Orange, Green/Purple, Teal/Pink. Colors often distinguish **status** rather than component type:
  - **Trainable**: warm tones (red, orange, deep pink)
  - **Frozen / static**: cool tones (grey, ice blue, cyan)
- **Highlights / results**: high saturation (primary red, bright gold) reserved for "error/loss", "ground truth", or the final output.

### B. Shapes & Containers
*Design philosophy: "Softened geometry." Sharp corners are for data; rounded corners are for processes.*

- **Process nodes (standard)**: rounded rectangles, corner radius 5–10 px. Dominant shape (~80%) for generic layers/steps.
- **Tensors & data**:
  - 3D stacks/cuboids imply depth/volume (e.g., B × H × W)
  - Flat squares/grids for matrices, tokens, or attention maps
  - Cylinders **exclusively** for databases, buffers, or memory
- **Grouping & hierarchy**:
  - "Macro-micro" pattern: a solid light-colored container for the global view, with a specific module connected to a "zoomed-in" detailed breakout box
  - Borders: **solid** for physical components; **dashed** for logical stages, optional paths, or scopes

### C. Lines & Arrows
*Design philosophy: Line style dictates flow type.*

- **Orthogonal / elbow (right angles)**: preferred for network architectures (implies precision, tensors)
- **Curved / Bezier**: preferred for system logic, feedback loops, or high-level data flow
- **Line semantics**:
  - Solid black/grey = forward pass / standard data flow
  - Dashed lines = "auxiliary flow" (gradient updates, skip connections, loss calculations)
  - Integrated math operators (⊕ Add, ⊗ Concat/Multiply) placed directly on the line or intersection

### D. Typography & Icons
*Design philosophy: Strict separation between "labeling" and "math".*

- **Labels (module names)**: sans-serif (Arial, Roboto, Helvetica). Bold for headers, regular for details.
- **Math variables**: serif (Times New Roman, LaTeX default). Variables (x, θ, ℒ) MUST be serif and italicized in the diagram.

**Iconography**
- Model state: 🔥/⚡ for trainable, ❄️/🔒/🛑 (greyed out) for frozen
- Operations: 🔍 inspection, ⚙️/🖥️ processing
- Content: 📄/💬 text/prompt, 🖼️ actual thumbnail (not just a square) for image

## 3. Common Pitfalls (how to look "amateur")

- ❌ **The "PowerPoint default" look**: standard Blue/Orange presets with heavy black outlines
- ❌ **Font mixing**: Times New Roman for "Encoder" labels (looks dated to the 1990s)
- ❌ **Inconsistent dimension**: mixing flat 2D boxes and 3D isometric cubes without reason (2D for logic + 3D for tensors is fine; random mixing is not)
- ❌ **Primary-color backgrounds**: saturated yellow/blue backgrounds for grouping (distracts from content)
- ❌ **Ambiguous arrows**: same line style for "data flow" and "gradient flow"

## 4. Domain-Specific Sub-styles

**Agent / LLM papers**: illustrative, narrative, "friendly", cartoony. UI aesthetics (chat bubbles, document icons). Cute 2D vector robots / human avatars / emojis to humanize agent reasoning steps.

**Computer vision / 3D papers**: spatial, dense, geometric. Frustums (camera cones), ray lines, point clouds. RGB color coding for axes/channels. Heatmaps (rainbow/viridis) for activation.

**Theoretical / optimization papers**: minimalist, abstract, "textbook". Focus on graph nodes (circles) and manifolds (planes/surfaces). Restrained palette — mostly grayscale with one highlight color (gold or blue). Avoid "cartoony" elements.

## 5. Aspect ratio (empirical rule from PaperBananaBench)

Methodology diagrams perform best at **landscape aspect ratios between 1.5 : 1 and 2.5 : 1**. Narrower than 1.5 forces cramped horizontal flow; wider than 2.5 is poorly supported by current image generation models.
