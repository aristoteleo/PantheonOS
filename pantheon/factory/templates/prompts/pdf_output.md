---
id: pdf_output
name: PDF Output Policy
description: |
  Mandatory policy for any PDF artefact requested by the user.
  LaTeX is the default — probe available engines, install if none.
  HTML print-to-PDF is forbidden as a default.
---

## PDF Output Policy (MANDATORY)

**Default to LaTeX for every report / document PDF.** When the user asks
for a report, paper, or any document-style PDF — "PDF", "pdf",
"导出 PDF", "生成 PDF", "compile PDF", "high-quality PDF", "report.pdf",
"paper.pdf", "分析报告 PDF", "调研报告", or any phrasing that requests a
`.pdf` file containing text/figures laid out as a document — you MUST
produce it via LaTeX. Browser print-to-PDF / HTML-to-PDF / `weasyprint`
/ `wkhtmltopdf` and similar HTML-rendering routes are **NOT acceptable
defaults**: they produce low-quality output with poor typography,
broken pagination, and inconsistent figure handling.

**Scope clarification — this policy targets document/report PDFs only.**
Single-figure PDFs emitted by plotting libraries (matplotlib, plotly,
cairo, etc.) for vector-editable artwork are **NOT** governed by this
rule and should keep using their native PDF backend. The policy is about
the document/report artefact that wraps text + figures, not the figure
files themselves.

The only situations in which HTML-print-to-PDF is acceptable are:
1. The user explicitly asks for an HTML report (not PDF), OR
2. The user explicitly opts out of LaTeX after you offered it, OR
3. LaTeX engine probing AND installation both fail (rare — record the
   failure reason before falling back).

### Step 1: Probe available LaTeX engines

Before writing any `.tex` file, check which engines are present. Use
`shell` to probe each candidate — **do NOT hard-code Tectonic** and do
NOT assume any specific tool is the right one:

```bash
for tool in tectonic xelatex lualatex pdflatex latexmk; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "FOUND: $tool ($(command -v $tool))"
  fi
done
```

Pick from what's actually available. The provided templates target
**XeTeX-family engines** (because they use `xeCJK` / Fandol fonts for
Chinese and `fontspec` for English typography). Preference order
when multiple engines are available:

| Rank | Engine | Notes |
|------|--------|-------|
| 1 | `tectonic` | One-shot, auto-downloads missing packages, no extra passes needed. Best when present. |
| 2 | `xelatex` (or `latexmk -xelatex`) | From a TeX Live / MacTeX install. Use `latexmk` if it's also present so cross-refs/citations resolve in one call. |
| 3 | `lualatex` | Acceptable substitute for XeTeX-family templates. |
| 4 | `pdflatex` | Last resort, and ONLY for pure-English content — it cannot render Chinese with the provided templates. If Chinese content is required and only `pdflatex` is available, install a XeTeX-capable engine instead (see Step 2). |

### Step 2: Install only if nothing is available

If Step 1 returns no usable engine, attempt to install — pick the
lightest option that works on the host OS:

- **Modal sandbox / Debian / Ubuntu**: try `tectonic` first (single
  binary, no `sudo` needed via `cargo` or prebuilt release tarball;
  `apt-get install -y tectonic` if root). Fall back to
  `apt-get install -y texlive-xetex texlive-latex-extra texlive-fonts-recommended texlive-lang-chinese`
  if Chinese is needed.
- **macOS**: `brew install tectonic` (or `brew install --cask mactex-no-gui`).
- **Anywhere with `cargo`**: `cargo install tectonic`.

Record the install command + output. If install fails after a
reasonable attempt (network blocked, no package manager, no `sudo`),
THEN — and only then — fall back to HTML print-to-PDF, and tell the
user explicitly that you couldn't get LaTeX working and why.

### Step 3: Compile

Generic invocation (substitute your chosen engine):

```bash
# Tectonic (preferred when available)
tectonic <name>.tex

# XeLaTeX via latexmk (auto-resolves cross-refs)
latexmk -xelatex -interaction=nonstopmode <name>.tex

# Bare XeLaTeX (may need 2 passes for cross-refs)
xelatex -interaction=nonstopmode <name>.tex
xelatex -interaction=nonstopmode <name>.tex
```

Place figures referenced from the `.tex` in the same working directory
and use relative paths. If the engine reports missing packages and
you're on TeX Live, install via `tlmgr install <pkg>` (or the host
package manager's equivalent) and re-run.

### Templates and skill

Use the `paper_writing` skill — templates live at
`~/.pantheon/skills/paper_writing/`:
- Chinese content → `latex_cn.md` (XeTeX + xeCJK + Fandol)
- English content → `latex_en.md` (XeTeX + fontspec, but degrades to
  pdflatex cleanly if you swap `\usepackage{fontspec}` out)
- Mixed → match the dominant body language.

### Team conventions

- **Paper Write Team**: delegate to the `reporter` agent — its triage
  already enforces this policy.
- **Teams without a reporter** (General Team, etc.): the leader (or
  whichever agent owns the artefact) runs the probe → install →
  compile pipeline itself via the `shell` toolset.
