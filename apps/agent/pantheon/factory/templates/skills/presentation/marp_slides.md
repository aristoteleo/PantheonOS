---
name: marp-slides
description: Build a presentation as a Marp markdown deck (`marp: true` frontmatter), with mermaid diagrams, dark theme, and the workarounds you actually need to make exports look right. Use when the user asks for slides / a deck / a talk / a presentation that they can show in a browser, export to PDF/PPTX, or hand off as a single .md file.
---

# Marp slides

Marp turns a single Markdown file into HTML/PDF/PPTX slides. Each slide is
separated by `---` on its own line. Renders cleanly in VS Code with the
Marp extension; exports via `marp file.md -o out.html` (or `--pdf` /
`--pptx`).

## Where to put the file

- Pick a `docs/talks/` or `docs/slides/` directory in the project root.
  Create it if missing.
- Filename: kebab-case, descriptive — e.g. `platform-design.md`,
  `q1-roadmap.md`. Don't reuse generic names like `slides.md`.

## Frontmatter (always include)

```yaml
---
marp: true
theme: default
paginate: true
size: 16:9
backgroundColor: "#0a0e17"
color: "#e2e8f0"
html: true   # required for raw <div class="mermaid"> blocks
style: |
  ... # see [template.md](template.md) for a complete starter
---
```

`html: true` is mandatory if you use mermaid or any inline HTML — Marp
strips it otherwise.

## Use [template.md](template.md) as the starting point

Copy the full frontmatter (including the `style:` block) and the example
slides verbatim, then replace content. The CSS in the template already
handles every gotcha listed below — don't write your own from scratch
unless the user explicitly wants a different look.

## Gotchas (each one cost me an hour the first time)

### Tables

Marp's default theme injects a zebra-stripe `tbody tr:nth-child(even)`
rule that turns alternating rows white on the dark background and
makes them unreadable. The template's `style:` block overrides:

```css
tbody tr:nth-child(even),
tbody tr:nth-child(odd) { background: transparent !important; }
th, td { background: transparent !important; color: #e2e8f0 !important; }
```

Always include this for dark themes.

### Mermaid diagrams

Marp does **not** render mermaid natively. The `mermaid: true` frontmatter
some tutorials mention only works in the experimental Marp CLI builds —
not in VS Code preview, not in standard exports. Use the **UMD CDN**
recipe instead:

1. In the frontmatter `style:` block, set the mermaid container styling
   (already in the template).
2. At the **bottom** of the file (after the last `---`), add:
   ```html
   <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
   <script>mermaid.initialize({ startOnLoad: true, theme: "dark" });</script>
   ```
3. Write each diagram as a single-line `<div class="mermaid">...</div>`
   block, **not** a triple-backtick mermaid fence. Marp wraps fenced
   code in `<pre>` which mermaid doesn't parse.
4. Inside the div, **no blank lines** — Marp inserts `<pre>` boundaries
   around blank lines mid-block and the diagram breaks. Keep the whole
   diagram on a single logical line; use literal `\n` inside node labels
   for line breaks (see #5).
5. For multi-line node labels, write `\n`, not `<br/>`. Marp HTML-decodes
   `<br/>` to `&lt;br/&gt;` before mermaid parses, so the tag never
   reaches the renderer.
6. For nested square brackets in nodes (e.g. circle node containing
   parens), use `[("text")]` with quoted content — bare `[(text)]` fails
   to parse on some node types.

### CSP / iframe loading

When opening the exported `.html` directly via `file://`, the browser
blocks the mermaid CDN script as a CSP violation. Two fixes:

- Serve the dir with `python3 -m http.server 8765` and open
  `http://localhost:8765/your-deck.html`.
- Or hand the user the .md file and tell them to use VS Code Marp
  preview (which serves over a local origin).

The template includes a comment at the top reminding the user of this.

### Diagram size

Without an explicit limit, mermaid can render an SVG taller than the
slide and overflow off-screen. Pin a max height in the `style:` block:

```css
.mermaid svg { max-height: 60vh !important; height: auto !important; width: auto !important; }
```

### Title slide

Use `<!-- _class: lead -->` directly above the H1 to centre the title
slide. This is a per-slide directive; without `_class: lead` the title
sits at the top-left like a regular slide.

```markdown
<!-- _class: lead -->
# My Talk
### Subtitle
```

### Page breaks

`---` on its own line creates a new slide. The triple-dash inside the
frontmatter or as a heading underline in the **first** slide doesn't
count as a break. Watch for accidental `---` in tables (use `|---|`
not `---` in the row above the body).

## Mermaid syntax pitfalls

The Marp-specific issues (`<br/>` decoded, blank lines mid-block, fence
vs `<div>`) are above. The list below is **mermaid itself** — same errors
hit you in any renderer, but Marp's preview gives almost no error info
so they're disproportionately painful here.

### Special characters in labels

Anything containing `( ) [ ] : ; # ,` must be wrapped in quotes:

```
A["solve.py: run macs3"]   ✅
A[solve.py: run macs3]     ❌  parser breaks at the colon
```

Nested brackets in shaped nodes (`[(...)]` cylinder, `((...))` circle)
also need quotes:

```
D[("DB (postgres)")]       ✅
D[(DB (postgres))]         ❌
```

### Reserved words as node IDs

`end`, `start`, `subgraph`, `class`, `state`, `direction`, `click` are
keywords. Never use them as IDs (they silently break the parse — `end`
in particular closes the enclosing subgraph mid-flow).

### Subgraphs

- Every `subgraph` needs a matching `end`, otherwise downstream nodes
  get sucked into it.
- Subgraph titles with spaces need quotes: `subgraph "Modal sandbox"`.
- Arrows go between **nodes**, never between subgraphs:
  ```
  A --> B          ✅  (A in sg1, B in sg2 — auto-routed)
  sg1 --> sg2      ❌
  ```

### Arrow label syntax

Three legal forms; don't mix them on the same line:

```
A -->|label| B           ✅
A -- label --> B         ✅
A --|label|--> B         ❌
```

### Repeated node IDs

The second definition is silently ignored — only the first label is
used. If you see "wrong text in a node," check for an earlier `A[...]`
above.

### Flowchart direction

Only `TD / TB / BT / LR / RL` are valid. `DT` (reverse of TD) is not.

### classDef ordering

`classDef` must come **before** any `class A myStyle` that references
it. Reversed order silently fails to apply the style.

### sequenceDiagram actor names with spaces

Use `participant` aliasing:

```
participant U as "End User"
U->>API: request          ✅
"End User"->>API: request ❌
```

### stateDiagram start / end

Use `[*]`, not `*`:

```
[*] --> Running
Running --> [*]
```

### `%%` comments must be on their own line

Inline `%%` is treated as label text, not a comment.

### Debug

- Marp preview's error reporting is essentially useless. When you see
  "Syntax error in mermaid diagram," copy the block into
  <https://mermaid.live> — that editor highlights the failing line.
- Mermaid major versions differ (`graph` only, vs `flowchart` from v9+);
  pin a version in the CDN script (`mermaid@10`) and stick with it.
- If a diagram is fighting you, splitting it across two slides usually
  reads better than fitting one mega-graph anyway.

## Export

```bash
# install once
npm i -g @marp-team/marp-cli

# HTML (most reliable for mermaid)
marp deck.md -o deck.html --html

# PDF (requires Chromium; mermaid renders if you used the CDN script)
marp deck.md --pdf --html

# PPTX (mermaid won't render — falls back to text)
marp deck.md --pptx --html
```

Add `docs/talks/*.{html,pdf,pptx}` to `.gitignore` — the .md is the
source, exports are derived.

## Tone & length

For an internal engineering talk: 12-18 slides, dense bullets, code
fragments where they say it better than prose. For a high-level
overview: 6-10 slides, one idea per slide, more whitespace. Always
ask the audience + duration before committing to a length.
