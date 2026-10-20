---
id: paper_writing_compile
name: LaTeX Compile Playbook
description: |
  Engine probe → install fallback → compile recipe for PDF output.
  Loaded by agents when they actually need to compile a .tex file.
---

# LaTeX Compile Playbook

## Step 1 — Probe available engines

```bash
for tool in tectonic xelatex lualatex pdflatex latexmk; do
  command -v "$tool" >/dev/null 2>&1 && echo "FOUND: $tool"
done
```

Pick from what's available. Preference order:

| Rank | Engine | Notes |
|------|--------|-------|
| 1 | `tectonic` | One-shot, auto-downloads missing packages. |
| 2 | `xelatex` (via `latexmk` if present) | Standard TeX Live install. Templates use xeCJK / fontspec — XeTeX-family required for Chinese. |
| 3 | `lualatex` | Acceptable XeTeX substitute. |
| 4 | `pdflatex` | Last resort; English-only — cannot render Chinese with the provided templates. |

## Step 2 — Install only if none available

- Debian/Ubuntu (Modal sandbox): `apt-get install -y tectonic` (or
  `cargo install tectonic`); fall back to
  `apt-get install -y texlive-xetex texlive-latex-extra texlive-fonts-recommended texlive-lang-chinese`.
- macOS: `brew install tectonic` or `brew install --cask mactex-no-gui`.

If install fails (no network, no sudo, no package manager) — record the
reason and only then fall back to HTML print-to-PDF.

## Step 3 — Compress figures before compile

Before compiling, downsize raster figures so the resulting PDF stays
reasonable (typical target: each figure ≲ 1 MB, max dimension ≲ 2000 px
at ~200–300 DPI for print). Skip vector formats (`.pdf`, `.svg`, `.eps`)
— they're already small and lose quality if rasterized.

```bash
# Probe what's available (ImageMagick is the most common)
command -v magick || command -v convert
command -v pngquant
command -v cwebp     # only if you can use webp; LaTeX usually wants PNG/JPG

# Example: downscale large PNG/JPG to max 2000px on the long edge
for f in figures/*.{png,jpg,jpeg}; do
  [ -f "$f" ] || continue
  magick "$f" -resize '2000x2000>' -quality 85 "$f"
done

# Example: lossy PNG quantization (huge gains on plot screenshots)
for f in figures/*.png; do
  pngquant --quality=70-90 --force --ext .png "$f"
done
```

Rules of thumb:
- Photographs / screenshots → JPEG at quality 80–85, max 2000 px.
- Plots / line art / diagrams → PNG with `pngquant`, or convert to
  vector (`.pdf` / `.svg`) if the source allows.
- Anything already > 1 MB after rendering is a candidate for
  compression; anything > 5 MB is mandatory.
- Replace the file in place (LaTeX references the same path).

If neither `magick`/`convert` nor `pngquant` is installed and figures
are clearly oversized, install one before compiling — e.g.
`apt-get install -y imagemagick pngquant` or `brew install imagemagick pngquant`.

## Step 4 — Compile

```bash
# Tectonic
tectonic <name>.tex

# latexmk with XeLaTeX (auto-resolves cross-refs)
latexmk -xelatex -interaction=nonstopmode <name>.tex

# Bare XeLaTeX (run twice for cross-refs)
xelatex -interaction=nonstopmode <name>.tex
xelatex -interaction=nonstopmode <name>.tex
```

Figures referenced from `.tex` must live in the same working directory
(use relative paths). For missing packages on TeX Live: `tlmgr install <pkg>`.
