---
id: msa
name: Multiple Sequence Alignment Viewer
description: |
  Open and drive a multiple-sequence-alignment view in the Pantheon
  sidebar — protein or DNA/RNA alignments rendered as a coloured grid.
  Built as a small self-contained renderer (ClustalX colours for protein,
  standard nucleotide colours for DNA / RNA) for reliability.
tags: [msa, multiple-sequence-alignment, protein-alignment, dna-alignment]
---

# Multiple Sequence Alignment Viewer

Show a pre-computed alignment (FASTA `.aln`, ClustalW, MUSCLE, MAFFT
output, ...) as an interactive coloured grid. Tip labels left, residues
right; scrolls horizontally for long alignments; a column-number ruler
at the top.

The renderer is a small custom HTML/CSS view rather than a heavy
JS-MSA-viewer dependency — it just works without finicky setup, and
agents can drive it with a simple state.

## When to use

- A protein or DNA alignment from MAFFT / MUSCLE / Clustal / MMseqs2 —
  inspect conservation visually.
- Compare a few orthologs at a key site / domain.
- Pair with `phylotree`: tree in one window, alignment in another.

For *building* the alignment, run MAFFT / MUSCLE on a FASTA via shell.
For very long alignments (> a few thousand columns), this view becomes
slow — fall back to a static PNG via matplotlib + `Bio.Align`.

## Quick demo — built in

```
desktop_open(app="msa", title="Hb alpha across vertebrates")
```

No `state` → opens the bundled `demo.json` (a vertebrate haemoglobin
α-chain alignment, 5 species).

## The state

```jsonc
{
  // REQUIRED — all sequences must be the same length (the alignment
  // invariant). Gaps are usually '-' or '.'.
  "sequences": [
    { "name": "Hb_alpha_human", "sequence": "VLSPADKTNVK--AAWGK..." },
    { "name": "Hb_alpha_mouse", "sequence": "VLSGEDKSNIK--AAWGK..." }
  ],

  // Optional display knobs (all have sensible defaults)
  "color_scheme":   "clustal",   // clustal (proteins) | nucleotide
                                 //   (DNA/RNA). Auto-detected from
                                 //   the residues if omitted.
  "tile_width":     18,          // px per column
  "tile_height":    22,          // px per row
  "label_width":    140,         // px reserved for sequence names
  "show_ruler":     true         // column-number track on top
}
```

### Colour schemes

- **clustal** — standard ClustalX colors for proteins: hydrophobic-blue,
  positive-red, negative-magenta, polar-green, glycine-orange,
  aromatic+H-cyan, proline-yellow. Gaps stay white.
- **nucleotide** — A-blue, T/U-yellow, G-orange, C-red.
- Auto-detection inspects the residues — if all characters are
  `[ACGTUN-.]`, picks `nucleotide`; otherwise `clustal`.

## From data → state

### From a FASTA alignment file

```python
def read_fasta(path):
    seqs, name, buf = [], None, []
    with open(path) as fp:
        for line in fp:
            line = line.rstrip()
            if not line: continue
            if line.startswith(">"):
                if name is not None:
                    seqs.append({"name": name, "sequence": "".join(buf)})
                name, buf = line[1:].split()[0], []
            else:
                buf.append(line)
        if name is not None:
            seqs.append({"name": name, "sequence": "".join(buf)})
    return seqs

seqs = read_fasta("alignment.aln.fasta")
desktop_open(app="msa", title="My alignment",
               state={"sequences": seqs, "color_scheme": "clustal"})
```

### From Bio.Align

```python
from Bio import AlignIO
aln = AlignIO.read("alignment.aln", "clustal")
seqs = [{"name": rec.id, "sequence": str(rec.seq)} for rec in aln]
desktop_open(app="msa", title="My alignment",
               state={"sequences": seqs})
```

### Quick on-the-fly MAFFT

```python
import subprocess
subprocess.run(["mafft", "--auto", "input.fasta"],
               stdout=open("aligned.fasta", "w"), check=True)
seqs = read_fasta("aligned.fasta")
desktop_open(app="msa", title="MAFFT result",
               state={"sequences": seqs})
```

## Driving the view

Replace whole `sequences` with `desktop_set`:

```python
# add a sequence to the alignment
get = desktop_read(window_id)
new_seqs = [*get["state"]["sequences"],
            {"name": "Hb_alpha_dog", "sequence": "VLSAADKAN..."}]
desktop_set(window_id, {"sequences": new_seqs})

# switch to a nucleotide alignment
desktop_set(window_id, {
    "sequences": [{"name":"v1","sequence":"ACGTACGT---"},
                  {"name":"v2","sequence":"ACGT-CGTGGT"}],
    "color_scheme": "nucleotide",
})
```

## Verify it

`desktop_read` — `status: ready`, empty `diagnostics`. Common
failure: sequences of unequal length (the adapter reports the offending
sequence by name via `lv.fail`). `desktop_screenshot` uses
html2canvas — clean since the renderer is pure HTML/CSS.

---

## Desktop runtime (Atrium)

This viewer is installed as the desktop app `msa` ("MSA Viewer"). Everything above drives it through the `desktop` tools, which reach every window of it — including ones the USER opened. What follows is what is specific to this app.

- **Open a file**: `desktop_open(path="/path/to/file")` — `.aln`, `.fasta`, `.fa` route here through the app's own open pipeline (format conversion, backend prepare) — no serve_local_data needed. Returns `window_id`.
- **Open by state**: `desktop_open(app="msa", state={...})` with the state contract documented above.
- **Force this viewer** for a file another app also claims: `desktop_open(app="msa", path=...)`. `desktop_apps()` lists every installed app with its id and file claims.
- **Drive any window** (yours or the user's): `desktop_windows()` lists them; `desktop_read(window_id)` returns the current state; `desktop_update(window_id, patch)` deep-merges; `desktop_call(window_id, action, args)` runs the same handlers the app's menus trigger. `desktop_call(w, "$close")` closes.
- **Fix in place**: when a view comes out wrong, correct THAT window (update/set/call, or `desktop_open(path=..., window_id=...)` for a different file) — do not open another window.

### Actions

- `setColors(scheme: clustal | nucleotide)` — Residue colour scheme
- `toggleRuler()` — Show/hide the column ruler
