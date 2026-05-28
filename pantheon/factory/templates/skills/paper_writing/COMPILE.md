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

## Step 3 — Compile

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
